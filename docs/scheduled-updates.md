# 自動更新與網站發佈（2026-10-14 跨日延遲修正）

主排程：每週一至五 UTC 13:05，即台北時間 21:05；不再把 UTC cron 與 timezone 混用。
補跑檢查：台北時間 21:35、22:05、22:35。GitHub 排程是盡力執行，可能延遲或漏發，並非精準到秒的服務承諾。

排程先將執行時間轉為台北時間，以既有主排程 21:05 作為完整行情（含投信、融資）的保守預期界線，並使用離線 TWSE 日曆尋找最近應已完成的交易日。
21:05 前從前一天往回找；21:05 起才納入當天。週末、官方休市一律往前找開市日，不把休市日或尚未完成的當日視為行情日期。
這是本專案的排程政策，不是 TWSE 保證資料到齊的時刻；真正缺漏仍須失敗。一次解析的 `expected_trading_date` 同時供快照與 freshness 檢查，執行中跨日不會改變目標。
延遲至休市日的排程可以補齊前一已完成交易日；若該日快照完整，完全不抓 TWSE。一般手動觸發的休市防護與明確 backfill 規則不變。
日曆缺年仍失敗關閉；2027 年須先核對並加入官方年度日曆。臨時休市仍須更新有官方來源的日曆紀錄。

目標日已具備 200 檔、不重複代號、有效價量欄位、同日來源、嚴格驗證通過及完整融資，補跑只重用，不新增 TWSE 請求或資料提交。
融資未齊仍保留既有補齊流程；手動更新的 15 分鐘 cooldown、freshness 與明確 backfill 規則不變。
排程取得非預期交易日資料時報錯並保留舊版；同日期的舊快照若缺欄位、缺驗證或缺融資，不能由 freshness 直接略過嚴格驗證，也不允許退回較舊行情。
既有 200 檔 MI_INDEX 價量與方向驗證、選股及排序內容不變。

資料由 GITHUB_TOKEN 提交後，明確要求現有 main/root GitHub Pages 建置；完成後讀取公開網站，逐一比對完整 dashboard JSON 與已提交版本。
已有同版建置則重用；網站仍舊、建置失敗、來源設定不符皆不宣告發佈成功。不修改 Pages 設定，不傳 GitHub token 給公開網站。
程式改版由 Publish tested dashboard release 先跑全套離線回歸，再發佈；與資料更新共用 concurrency lock。

## 檢查方式

- Daily Taiwan Stock Update：確認 event=schedule、台北啟動時間、calendar/schedule/freshness summary。
- [validate] PASSED YYYY-MM-DD 200：200 檔行情驗證通過。
- [pages] VERIFIED commit=... trading_date=... stocks=200：公開網站資料與 Git 已提交資料相符。

## 跨日事故回歸

2026-10-13 台北 05:41、05:50、06:03 的 runs `38312186442`、`38313099497`、`38314435962`，
原本均以 10/13 要求尚未開盤的行情，對 10/12 報 `SCHEDULE FRESHNESS FAILED`。
三次日誌摘錄保存在 `tests/fixtures/schedule_delay_20261013.json`；修正後目標均為 10/12。
離線測試另涵蓋四次準時排程、21:05 邊界、午夜、開盤前／盤中、連假／週末、跨年度缺日曆、
快照重用零行情請求、缺失／損壞快照、同日不完整資料，以及真正過期／未來日期仍須失敗。
本次不重算 10/13 行情；沿用已通過 200 檔驗證的已提交資料。

## 依據

https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
https://docs.github.com/en/rest/pages/pages#request-a-github-pages-build
https://www.twse.com.tw/zh/trading/holiday.html

