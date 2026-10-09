# 2026-10-09 自動更新與網站發佈修正

主排程：每週一至五 UTC 13:05，即台北時間 21:05；不再把 UTC cron 與 timezone 混用。
補跑檢查：台北時間 21:35、22:05、22:35。GitHub 排程是盡力執行，可能延遲或漏發，並非精準到秒的服務承諾。

每次先依執行當下的台北日期，使用既有離線 TWSE 日曆判斷。週末、官方休市不抓行情、不改資料；2026-10-09 與 10-26 休市。
日曆缺年仍失敗關閉；2027 年須先核對並加入官方年度日曆。臨時休市仍須更新有官方來源的日曆紀錄。

當天已具備 200 檔、不重複代號、同日來源、嚴格驗證通過及完整融資，補跑只重用，不新增 TWSE 請求或資料提交。
融資未齊仍保留既有補齊流程；手動更新的 15 分鐘 cooldown、freshness 與明確 backfill 規則不變。
排程取得非預期當日資料時報錯並保留舊版，不再把「仍只有昨天資料」當成本日更新成功。
既有 200 檔 MI_INDEX 價量與方向驗證、選股及排序內容不變。

資料由 GITHUB_TOKEN 提交後，明確要求現有 main/root GitHub Pages 建置；完成後讀取公開網站，逐一比對完整 dashboard JSON 與已提交版本。
已有同版建置則重用；網站仍舊、建置失敗、來源設定不符皆不宣告發佈成功。不修改 Pages 設定，不傳 GitHub token 給公開網站。
程式改版由 Publish tested dashboard release 先跑全套離線回歸，再發佈；與資料更新共用 concurrency lock。

## 檢查方式

- Daily Taiwan Stock Update：確認 event=schedule、台北啟動時間、calendar/schedule/freshness summary。
- [validate] PASSED YYYY-MM-DD 200：200 檔行情驗證通過。
- [pages] VERIFIED commit=... trading_date=... stocks=200：公開網站資料與 Git 已提交資料相符。

## 依據

https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
https://docs.github.com/en/rest/pages/pages#request-a-github-pages-build
https://www.twse.com.tw/zh/trading/holiday.html
