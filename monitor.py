#!/usr/bin/env python3
"""
拓元售票 (tixCraft) 票券監控系統
使用 Playwright 瀏覽器抓取頁面，有票時透過 Google Chat Webhook 通知

用法:
  本機持續監控:  python3 monitor.py
  本機登入:      python3 monitor.py login
  CI 單次檢查:   python3 monitor.py check  (GitHub Actions 用)
"""

from __future__ import annotations

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


# ── 載入設定（支援環境變數覆蓋，給 GitHub Actions 用）─────


def load_config() -> dict:
    # CI 模式：純用環境變數，不需要 config.json
    if os.environ.get("TARGET_URL") and os.environ.get("GOOGLE_CHAT_WEBHOOK"):
        return {
            "target_url": os.environ["TARGET_URL"],
            "google_chat_webhook": os.environ["GOOGLE_CHAT_WEBHOOK"],
            "check_interval_seconds": 30,
        }
    # 本機模式：讀 config.json
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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


def check_once(context: BrowserContext, config: dict) -> tuple[bool, str]:
    url = config["target_url"]
    html = fetch_page_with_playwright(context, url)
    if html is None:
        return False, "無法取得頁面"

    areas = parse_ticket_areas(html)
    if not areas:
        return False, "無法解析頁面內容"

    no_ticket_keywords = ["無票", "已售完", "sold out", "暫無", "目前無可售"]
    available = []
    for area in areas:
        status_lower = area["status"].lower()
        is_sold_out = any(kw in status_lower for kw in no_ticket_keywords)
        if not is_sold_out and area["status"]:
            available.append(area)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if available:
        lines = [f"🎫 *拓元有票通知!* ({now})", f"🔗 {url}", ""]
        for a in available:
            lines.append(f"  • {a['name']}: {a['status']}")
        return True, "\n".join(lines)
    else:
        summary = ", ".join(f"{a['name']}:{a['status']}" for a in areas)
        return False, f"[{now}] 目前無票 — {summary}"


# ── CI 單次檢查模式（GitHub Actions 用）─────────────────────


def run_ci_check():
    """單次檢查，適合 cron 排程"""
    config = load_config()
    webhook = config["google_chat_webhook"]

    log.info("CI 單次檢查: %s", config["target_url"])

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            locale="zh-TW",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        has_ticket, msg = check_once(context, config)

        if has_ticket:
            log.info("偵測到有票！發送通知...")
            send_google_chat(webhook, msg)
        else:
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

    log.info("=== 拓元票券監控啟動 ===")
    log.info("目標: %s", config["target_url"])
    log.info("檢查間隔: %d 秒", interval)
    log.info("按 Ctrl+C 停止")

    send_google_chat(webhook, f"🟢 拓元監控已啟動\n目標: {config['target_url']}\n間隔: {interval} 秒")

    with sync_playwright() as pw:
        if not USER_DATA_DIR.exists():
            login_flow(pw)

        context = create_browser_context(pw, headless=True)
        last_notified = False

        try:
            while True:
                config = load_config()
                webhook = config["google_chat_webhook"]

                has_ticket, msg = check_once(context, config)

                if has_ticket:
                    log.info("偵測到有票！")
                    send_google_chat(webhook, msg)
                    last_notified = True
                else:
                    log.info(msg)
                    if last_notified:
                        send_google_chat(webhook, f"⚪ 票券已無 — {datetime.now().strftime('%H:%M:%S')}")
                        last_notified = False

                time.sleep(interval)

        except KeyboardInterrupt:
            log.info("\n=== 監控已停止 ===")
            send_google_chat(webhook, "🔴 拓元監控已停止")
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
