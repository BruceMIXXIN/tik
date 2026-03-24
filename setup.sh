#!/bin/bash
# ============================================
# 拓元票券監控 - Mac 一鍵安裝腳本
# ============================================

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CURRENT_USER="$(whoami)"
PLIST_NAME="com.${CURRENT_USER}.tixcraft-monitor"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
PYTHON="$(which python3)"

echo "=========================================="
echo "  拓元票券監控 - 安裝設定"
echo "=========================================="
echo ""
echo "📁 專案路徑: $PROJECT_DIR"
echo "👤 使用者:   $CURRENT_USER"
echo "🐍 Python:   $PYTHON"
echo ""

# ── 1. 安裝 Python 依賴 ──
echo "📦 安裝 Python 依賴..."
pip3 install -r "$PROJECT_DIR/requirements.txt"
echo ""

# ── 2. 安裝 Playwright 瀏覽器 ──
echo "🌐 安裝 Playwright Chromium..."
python3 -m playwright install chromium
echo ""

# ── 3. 設定 config.json ──
CONFIG_FILE="$PROJECT_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "⚙️  設定 config.json..."
    echo ""
    read -p "🔗 請貼上 Google Chat Webhook URL: " WEBHOOK_URL
    echo ""
    echo "📊 網址來源（二擇一）:"
    echo "   1) Google Sheet（推薦，可在 Sheet 上切換網址）"
    echo "   2) 直接輸入單一網址"
    read -p "請選擇 (1/2): " URL_MODE
    echo ""

    SHEET_ID=""
    TARGET_URL=""
    if [ "$URL_MODE" = "1" ]; then
        read -p "📊 請貼上 Google Sheet 網址或 ID: " SHEET_INPUT
        # 從完整網址中提取 Sheet ID
        SHEET_ID=$(echo "$SHEET_INPUT" | sed -n 's|.*spreadsheets/d/\([^/]*\).*|\1|p')
        if [ -z "$SHEET_ID" ]; then
            SHEET_ID="$SHEET_INPUT"
        fi
        echo "   Sheet ID: $SHEET_ID"
        echo "   ⚠️  請確認 Sheet 已設為「知道連結的人都能檢視」"
    else
        read -p "🎫 請貼上要監控的拓元網址: " TARGET_URL
    fi
    echo ""
    read -p "⏱️  檢查間隔秒數 (預設 30): " INTERVAL
    INTERVAL=${INTERVAL:-30}

    cat > "$CONFIG_FILE" << JSONEOF
{
  "google_sheet_id": "$SHEET_ID",
  "target_url": "$TARGET_URL",
  "google_chat_webhook": "$WEBHOOK_URL",
  "check_interval_seconds": $INTERVAL
}
JSONEOF
    echo "✅ config.json 已建立"
else
    echo "✅ config.json 已存在，跳過"
fi
echo ""

# ── 4. 登入拓元 ──
if [ ! -d "$PROJECT_DIR/browser_data" ]; then
    echo "🔐 首次使用，需要登入拓元..."
    python3 "$PROJECT_DIR/monitor.py" login
else
    echo "✅ 已有登入資料，跳過"
fi
echo ""

# ── 5. 產生 LaunchAgent plist（自動偵測路徑）──
echo "🔧 產生背景服務設定..."
cat > "$PLIST_PATH" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${PROJECT_DIR}/monitor.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${PROJECT_DIR}/monitor.log</string>

    <key>StandardErrorPath</key>
    <string>${PROJECT_DIR}/monitor.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:${HOME}/Library/Python/3.9/bin:${HOME}/.local/bin</string>
    </dict>
</dict>
</plist>
PLISTEOF

echo "✅ plist 已寫入: $PLIST_PATH"
echo ""

# ── 6. 啟動背景服務 ──
read -p "🚀 要現在啟動背景監控嗎？(y/n) " START_NOW
if [ "$START_NOW" = "y" ] || [ "$START_NOW" = "Y" ]; then
    # 先卸載舊的（如果有）
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    launchctl load "$PLIST_PATH"
    echo ""
    echo "✅ 背景監控已啟動！"
else
    echo ""
    echo "📝 稍後可手動啟動:"
    echo "   launchctl load $PLIST_PATH"
fi

echo ""
echo "=========================================="
echo "  安裝完成！"
echo "=========================================="
echo ""
echo "📋 常用指令:"
echo "  看 log:     tail -f $PROJECT_DIR/monitor.log"
echo "  停止服務:   launchctl unload $PLIST_PATH"
echo "  重啟服務:   launchctl unload $PLIST_PATH && launchctl load $PLIST_PATH"
echo "  改網址:     直接改 Google Sheet，不用重啟！"
echo "  改設定:     nano $PROJECT_DIR/config.json"
echo "  重新登入:   python3 $PROJECT_DIR/monitor.py login"
echo ""
