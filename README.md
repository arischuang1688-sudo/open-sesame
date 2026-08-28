# AI 台股六條件選股 Dashboard

GitHub Pages + GitHub Actions 每日盤後自動更新。

## 部署
1. 建立 GitHub repository。
2. 將本專案所有檔案上傳。
3. Settings → Pages → Source 選 GitHub Actions。
4. Actions 手動執行一次 `Daily Taiwan Stock Update`。
5. 之後平日每天自動更新。

本版本使用 TWSE 公開盤後資料；後續可擴充 TPEx、完整歷史K線與個股融資資料。
「優先買進」只是量化篩選，不保證獲利。
