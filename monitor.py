#!/usr/bin/env python3
"""
拓元售票 (tixCraft) 票券監控系統
使用 Playwright 瀏覽器抓取頁面，有票時透過 Google Chat Webhook 通知
支援從 Google Sheet 讀取多個監控網址，可在 Sheet 上一鍵開關

用法:
  本機持續監控:  python3 monitor.py
  本機登入:      python3 monitor.py login
  CI 單次檢查:   python3 monitor.py check  (GitHub Actions 用)
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
import sys
import logging
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, BrowserContext

# ── 設定 ──────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent / "config.json"
USER_DATA_DIR = Path(__file__).parent / "browser_data"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


EXCLUDED_AREA_KEYWORDS = ["身障", "身心障礙", "輪椅", "愛心席", "disabled", "wheelchair"]


# ── 載入設定（支援環境變數覆蓋，給 GitHub Actions 用）─────


def load_config():
    # CI 模式：純用環境變數，不需要 config.json
    if os.environ.get("GOOGLE_CHAT_WEBHOOK"):
        return {
            "google_sheet_id": os.environ.get("GOOGLE_SHEET_ID", ""),
            "target_url": os.environ.get("TARGET_URL", ""),
            "google_chat_webhook": os.environ["GOOGLE_CHAT_WEBHOOK"],
            "check_interval_seconds": 30,
            "targets": [
                {
                    "url": os.environ["TARGET_URL"],
                    "name": "CI Target",
                    "enabled": True,
                }
            ],
        }
    # 本機模式：讀 config.json
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    # 向下相容：舊格式 target_url -> 新格式 targets
    if "target_url" in config and "targets" not in config:
        config["targets"] = [
            {
                "url": config.pop("target_url"),
                "name": "預設目標",
                "enabled": True,
            }
        ]
        save_config(config)

    return config


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ── Google Sheet 讀取 ─────────────────────────────────


def fetch_urls_from_sheet(sheet_id):
    """從 Google Sheet 讀取啟用的網址清單"""
    csv_url = (
        "https://docs.google.com/spreadsheets/d/"
        "{}/export?format=csv&gid=0".format(sheet_id)
    )
    try:
        resp = requests.get(csv_url, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        reader = csv.reader(io.StringIO(resp.text))
        header = next(reader, None)
        if not header:
            log.warning("Google Sheet 是空的")
            return []

        urls = []
        for row in reader:
            if len(row) < 2:
                continue
            url = row[0].strip()
            enabled = row[1].strip().lower()
            if url and enabled in ("true", "yes", "y", "1", "是", "開啟", "o", "on"):
                urls.append(url)

        log.info("從 Google Sheet 讀到 %d 個啟用的網址", len(urls))
        return urls
    except requests.RequestException as e:
        log.error("無法讀取 Google Sheet: %s", e)
        return []


def get_target_urls(config):
    """取得要監控的網址清單（優先 Google Sheet，fallback 到 config）"""
    sheet_id = config.get("google_sheet_id", "")
    if sheet_id:
        urls = fetch_urls_from_sheet(sheet_id)
        if urls:
            return urls
        log.warning("Google Sheet 沒有啟用的網址，嘗試 fallback 到 config")

    # fallback: 從 config.json 的 target_url
    target = config.get("target_url", "")
    if target:
        return [target]
    return []


# ── Playwright 瀏覽器 ───────────────────────────────────


def create_browser_context(playwright, headless: bool = True) -> BrowserContext:
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        headless=headless,
        locale="zh-TW",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )
    return context


def login_flow(playwright) -> None:
    log.info("開啟瀏覽器，請登入拓元並通過驗證...")
    context = create_browser_context(playwright, headless=False)
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://tixcraft.com/", timeout=30000)
    log.info("請在瀏覽器中登入拓元，完成後按 Enter 繼續...")
    input()
    context.close()
    log.info("登入資訊已儲存")


def fetch_page_with_playwright(context: BrowserContext, url: str) -> str | None:
    page = None
    try:
        page = context.new_page()
        page.goto(url, timeout=20000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        html = page.content()
        return html
    except Exception as e:
        log.error("Playwright 抓取失敗: %s", e)
        return None
    finally:
        if page:
            page.close()


# ── 解析票券資訊 ──────────────────────────────────────────


def parse_ticket_areas(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    areas = []

    # 先檢查是否被擋（驗證頁、CAPTCHA、403 等）
    page_text = soup.get_text()
    block_signals = [
        "Identity Verified", "not a bot", "are you human",
        "captcha", "CAPTCHA", "Access Denied", "403 Forbidden",
        "Please verify", "security check",
    ]
    for signal in block_signals:
        if signal in page_text:
            areas.append({"name": "⚠️ 被擋", "status": "驗證頁面/機器人偵測"})
            return areas

    # 方法1: 表格或列表
    area_list = soup.select("table.table tbody tr, .area-list li, .zone-list li")
    if area_list:
        for row in area_list:
            name_el = row.select_one("td:first-child, .area-name, .zone-name, a")
            count_el = row.select_one("td:last-child, .remaining, .count, font")
            name = name_el.get_text(strip=True) if name_el else row.get_text(strip=True)
            count_text = count_el.get_text(strip=True) if count_el else ""
            areas.append({"name": name, "status": count_text})
        return areas

    # 方法2: 連結按鈕
    buttons = soup.select('a[href*="ticket/order"], button.btn, .btn-area')
    if buttons:
        for btn in buttons:
            areas.append({"name": btn.get_text(strip=True), "status": "可選位"})
        return areas

    # 方法3: 無票訊息
    no_ticket_msgs = ["目前無可售", "暫無", "已售完", "No tickets", "sold out", "沒有可售"]
    page_text = soup.get_text()
    for msg in no_ticket_msgs:
        if msg in page_text:
            areas.append({"name": "全場", "status": "無票"})
            return areas

    # 方法4: 頁面摘要
    body_text = page_text[:500].strip()
    if body_text:
        areas.append({"name": "頁面摘要", "status": body_text[:200]})

    return areas


def is_excluded_area(area_name: str) -> bool:
    name = (area_name or "").lower()
    return any(keyword.lower() in name for keyword in EXCLUDED_AREA_KEYWORDS)


# ── Google Chat 通知 ──────────────────────────────────────


def send_google_chat(webhook_url: str, message: str) -> bool:
    payload = {"text": message}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        log.info("Google Chat 通知已發送")
        return True
    except requests.RequestException as e:
        log.error("Google Chat 通知失敗: %s", e)
        return False


# ── 核心檢查 ──────────────────────────────────────────────


def check_single_url(context, url):
    """檢查單一網址，回傳 (has_ticket, message)"""
    html = fetch_page_with_playwright(context, url)
    if html is None:
        return False, f"[{name}] 無法取得頁面"

    areas = parse_ticket_areas(html)
    if not areas:
        return False, f"[{name}] 無法解析頁面內容"

    filtered_areas = [area for area in areas if not is_excluded_area(area.get("name", ""))]
    if not filtered_areas:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return False, f"[{now}] {url} — 僅偵測到身障區，已排除"

    no_ticket_keywords = ["無票", "已售完", "sold out", "暫無", "目前無可售", "被擋", "驗證頁面"]
    available = []
    for area in filtered_areas:
        status_lower = area["status"].lower()
        is_sold_out = any(kw in status_lower for kw in no_ticket_keywords)
        if not is_sold_out and area["status"]:
            available.append(area)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if available:
        lines = ["\U0001f3ab *拓元有票通知!* ({})".format(now), "\U0001f517 {}".format(url), ""]
        for a in available:
            lines.append("  \u2022 {}: {}".format(a["name"], a["status"]))
        return True, "\n".join(lines)
    else:
        summary = ", ".join("{}:{}".format(a["name"], a["status"]) for a in filtered_areas)
        return False, "[{}] {} \u2014 {}".format(now, url, summary)


def check_all_urls(context, config):
    """檢查所有啟用的網址，回傳 (any_ticket, messages)"""
    urls = get_target_urls(config)
    if not urls:
        return False, ["沒有要監控的網址（Google Sheet 為空或 config 未設定）"]

    any_ticket = False
    ticket_msgs = []
    no_ticket_msgs = []

    for url in urls:
        has_ticket, msg = check_single_url(context, url)
        if has_ticket:
            any_ticket = True
            ticket_msgs.append(msg)
        else:
            no_ticket_msgs.append(msg)

    return any_ticket, ticket_msgs, no_ticket_msgs


# ── CI 單次檢查模式（GitHub Actions 用）─────────────────────


def run_ci_check():
    """單次檢查，適合 cron 排程"""
    config = load_config()
    webhook = config["google_chat_webhook"]

    urls = get_target_urls(config)
    log.info("CI 單次檢查: %d 個網址", len(urls))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            locale="zh-TW",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        any_ticket, ticket_msgs, no_ticket_msgs = check_all_urls(context, config)

        if any_ticket:
            for msg in ticket_msgs:
                log.info("偵測到有票！發送通知...")
                send_google_chat(webhook, msg)
        else:
            for msg in no_ticket_msgs:
                log.info(msg)

        context.close()
        browser.close()


# ── 本機持續監控模式 ──────────────────────────────────────


def run_local():
    config = load_config()
    webhook = config["google_chat_webhook"]
    interval = config.get("check_interval_seconds", 30)

    if "YOUR_WEBHOOK_URL_HERE" in webhook:
        log.error("請先在 config.json 填入 Google Chat Webhook URL")
        sys.exit(1)

    urls = get_target_urls(config)
    sheet_id = config.get("google_sheet_id", "")
    source = "Google Sheet" if sheet_id else "config.json"

    log.info("=== 拓元票券監控啟動 ===")
    log.info("來源: %s", source)
    log.info("監控中: %d 個網址", len(urls))
    for u in urls:
        log.info("  -> %s", u)
    log.info("檢查間隔: %d 秒", interval)
    log.info("按 Ctrl+C 停止")

    startup_msg = "\U0001f7e2 拓元監控已啟動\n來源: {}\n監控: {} 個網址\n間隔: {} 秒".format(
        source, len(urls), interval
    )
    send_google_chat(webhook, startup_msg)

    with sync_playwright() as pw:
        if not USER_DATA_DIR.exists():
            login_flow(pw)

        context = create_browser_context(pw, headless=True)
        last_notified_urls = set()

        try:
            while True:
                # 每次迴圈重讀設定 + Google Sheet，這樣改 Sheet 不用重啟
                config = load_config()
                webhook = config["google_chat_webhook"]

                any_ticket, ticket_msgs, no_ticket_msgs = check_all_urls(context, config)

                # 有票的發通知
                current_ticket_urls = set()
                if any_ticket:
                    for msg in ticket_msgs:
                        log.info("偵測到有票！")
                        send_google_chat(webhook, msg)
                        # 從訊息中提取 URL
                        for line in msg.split("\n"):
                            if "tixcraft.com" in line:
                                current_ticket_urls.add(line.strip().replace("\U0001f517 ", ""))

                # 之前有票但現在沒了的，發一次通知
                disappeared = last_notified_urls - current_ticket_urls
                if disappeared:
                    now_str = datetime.now().strftime("%H:%M:%S")
                    send_google_chat(webhook, "\u26aa 票券已無 \u2014 {}".format(now_str))

                last_notified_urls = current_ticket_urls

                # log 無票的
                for msg in no_ticket_msgs:
                    log.info(msg)

                time.sleep(interval)

        except KeyboardInterrupt:
            log.info("\n=== 監控已停止 ===")
            send_google_chat(webhook, "\U0001f534 拓元監控已停止")
        finally:
            context.close()


# ── 入口 ─────────────────────────────────────────────────


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"

    if cmd == "login":
        with sync_playwright() as pw:
            login_flow(pw)
    elif cmd == "check":
        run_ci_check()
    else:
        run_local()


if __name__ == "__main__":
    main()
