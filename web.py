#!/usr/bin/env python3
"""
拓元監控 Web Dashboard
用來管理多個監控網址（新增/刪除/啟停）

用法: python3 web.py
開啟瀏覽器: http://localhost:5000
"""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

CONFIG_PATH = Path(__file__).parent / "config.json"

app = Flask(__name__)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/targets", methods=["GET"])
def get_targets():
    config = load_config()
    return jsonify(config.get("targets", []))


@app.route("/api/targets", methods=["POST"])
def add_target():
    data = request.get_json()
    url = data.get("url", "").strip()
    name = data.get("name", "").strip()

    if not url:
        return jsonify({"error": "URL 不可為空"}), 400
    if not url.startswith("https://tixcraft.com/"):
        return jsonify({"error": "僅支援 tixcraft.com 網址"}), 400

    config = load_config()
    targets = config.get("targets", [])

    if any(t["url"] == url for t in targets):
        return jsonify({"error": "此網址已存在"}), 409

    targets.append({"url": url, "name": name or url.split("/")[-1], "enabled": True})
    config["targets"] = targets
    save_config(config)
    return jsonify({"ok": True}), 201


@app.route("/api/targets/<int:idx>", methods=["DELETE"])
def delete_target(idx: int):
    config = load_config()
    targets = config.get("targets", [])

    if idx < 0 or idx >= len(targets):
        return jsonify({"error": "索引超出範圍"}), 404

    removed = targets.pop(idx)
    config["targets"] = targets
    save_config(config)
    return jsonify({"ok": True, "removed": removed})


@app.route("/api/targets/<int:idx>", methods=["PATCH"])
def toggle_target(idx: int):
    config = load_config()
    targets = config.get("targets", [])

    if idx < 0 or idx >= len(targets):
        return jsonify({"error": "索引超出範圍"}), 404

    data = request.get_json()
    if "enabled" in data:
        targets[idx]["enabled"] = bool(data["enabled"])
    if "name" in data:
        targets[idx]["name"] = data["name"]

    config["targets"] = targets
    save_config(config)
    return jsonify({"ok": True, "target": targets[idx]})


@app.route("/api/config", methods=["GET"])
def get_config():
    config = load_config()
    return jsonify({
        "check_interval_seconds": config.get("check_interval_seconds", 30),
        "has_webhook": "YOUR_WEBHOOK_URL_HERE" not in config.get("google_chat_webhook", ""),
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
