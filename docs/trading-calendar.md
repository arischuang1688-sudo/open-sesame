# 台股交易日閘門

`main.yml` 在任何 TWSE 資料請求之前，執行 `scripts/trading_day.py`。
使用執行當下的 **Asia/Taipei** 日期及版本控管中的官方年度日曆，完全離線判斷。
週末、國定假日、補假、春節前僅結算交割的日期均跳過資料產生、驗證、排序及發布。
官方表內的「開始交易日」「最後交易日」仍是開市日，不能把每一列都當成休市。

## 人工補資料

在 GitHub Actions → Daily Taiwan Stock Update → Run workflow 勾選 `backfill`，
可於已知休市日補抓 **TWSE 最新可得交易日** 的漏更資料，或補齊同日個股融資。
未勾選的手動觸發（包含現有網頁按鈕）於休市日也直接跳過。
這個選項不是指定任意歷史日期，也不允許強制覆寫舊行情。
15 分鐘冷卻、只接受新交易日／同日融資筆數增加的 freshness 例外、
全部 200 檔 MI_INDEX 嚴格驗證仍適用，排程不能使用這個例外。

## 排程延遲與重複發布

- 保留原 `5 21 * * 1-5`、`Asia/Taipei` 與 `open-sesame-data-update` concurrency group。
- checkout 明確讀取執行分支最新 HEAD；排隊中的工作不會用觸發時的舊 dashboard 判斷冷卻／freshness。
- 以實際執行日期判斷；若星期四的排程拖到星期五休市才執行，會跳過，可用上述人工補資料。
- freshness 的 `GITHUB_OUTPUT` 使用真正換行，`skip=true` 才能正確阻擋同日重複發布。
- 發布必須明確收到 calendar 與 freshness 的 `skip=false`；缺失輸出不放行。
- 不觸發 `.github/force-update`，不修改保留的 `validated-now.yml`、選股條件或 dashboard schema。
  `validated-now.yml` 是獨立的既有人工維護入口，不屬於本閘門；日常補資料請使用 `main.yml`。

## 年度與臨時休市維護

日曆檔案為 `data/trading_calendar/twse-YYYY.json`。目前核實並隨程式保存的是 **2026 年**。
在年底前從證交所「市場開休市日期」取得下一年度公告，逐列核對後新增日曆檔，
更新 `year`、完整年度 `coverage`、`verified_on`、`sources` 及 `entries`。
沒有取得可核實的官方資料時，不從政府辦公日曆或前一年假日推估。

缺少年度、檔案損壞或內容格式異常會以 `CALENDAR FAILED` 令工作失敗，
不發出 TWSE 請求、不發布；`backfill` 也不能略過這個錯誤。
2027 年資料未納入前，2027 年工作會明確停止，必須先補入官方日曆。

年度表不能預知颱風等臨時休市。依證交所正式公告在 `entries` 增加／修正日期，
並於 `sources` 補上公告連結後提交；無須修改 Python。
在快照尚未更新前，既有 freshness 與同交易日驗證仍作為資料發布的後續保護，
但不能保證省下該次臨時休市日的網路請求。

## 驗證

```sh
python -m unittest discover -s tests -p 'test_*.py'
```

測試使用真實 workflow 的冷卻／freshness 程式與 `if` 表達式，涵蓋開市、休市、
人工補資料、台北跨午夜／跨年度、未知年度及損壞日曆，以及跳過時不呼叫產生器或發布。
不需要連線 TWSE。推送程式只執行離線回歸測試，不額外觸發資料更新。
