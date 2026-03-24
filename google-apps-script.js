/**
 * Google Apps Script — 部署為 Web App
 * 讓前端可以新增/刪除 Google Sheet 中的監控網址
 *
 * 部署步驟：
 * 1. 打開你的 Google Sheet → 延伸功能 → Apps Script
 * 2. 把這段程式碼貼進去，取代原本的 Code.gs
 * 3. 點「部署」→「新增部署」→ 類型選「網頁應用程式」
 * 4. 「誰可以存取」選「所有人」
 * 5. 按「部署」，複製產生的 Web App 網址
 * 6. 把網址貼到前端的「Apps Script URL」欄位
 */

function doGet(e) {
  return ContentService.createTextOutput("OK");
}

function doPost(e) {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("工作表1")
    || SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];

  var data = JSON.parse(e.postData.contents);
  var action = data.action;

  if (action === "add") {
    var url = data.url || "";
    var enabled = data.enabled || "是";
    if (!url) {
      return jsonResponse({ error: "URL 不可為空" });
    }
    // 檢查是否已存在
    var urls = sheet.getRange(2, 1, Math.max(sheet.getLastRow() - 1, 1), 1).getValues();
    for (var i = 0; i < urls.length; i++) {
      if (urls[i][0] === url) {
        return jsonResponse({ error: "此網址已存在" });
      }
    }
    sheet.appendRow([url, enabled]);
    return jsonResponse({ ok: true, message: "已新增" });
  }

  if (action === "delete") {
    var targetUrl = data.url || "";
    if (!targetUrl) {
      return jsonResponse({ error: "URL 不可為空" });
    }
    var lastRow = sheet.getLastRow();
    for (var r = 2; r <= lastRow; r++) {
      if (sheet.getRange(r, 1).getValue() === targetUrl) {
        sheet.deleteRow(r);
        return jsonResponse({ ok: true, message: "已刪除" });
      }
    }
    return jsonResponse({ error: "找不到此網址" });
  }

  return jsonResponse({ error: "未知的 action" });
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
