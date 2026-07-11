# NCKUall Frontend E2E

本測試套件使用 Playwright Test 與 Page Object Model，涵蓋：

- 遊客從首頁進入職涯規劃、切換科系、查看教授卡片與預填 AI 提問。
- 遊客進入生活助手後，評論表單的 RBAC 禁用與提交攔截。
- ISR webhook 拒絕錯誤密鑰並接受 FastAPI 的合法 revalidation 事件。
- Chromium 桌面版與 Pixel 7 行動版。

## 第一次安裝

```bash
npm install
npm run test:e2e:install
```

## 執行方式

```bash
# Headless，執行桌面與行動版
npm run test:e2e

# 顯示瀏覽器
npm run test:e2e:headed

# Playwright 互動式介面
npm run test:e2e:ui

# 僅執行桌面 Chromium
npm run test:e2e -- --project=chromium-desktop

# 開啟最近一次 HTML 報告
npm run test:e2e:report
```

Playwright 會自動在 `http://127.0.0.1:3100` 啟動 Next.js。測試使用
`e2e/fixtures/api-mocks.ts` 攔截後端 API，因此不需要啟動 FastAPI，也不會寫入正式
Supabase。失敗時會保留 screenshot、video 與 trace 到 `test-results/`。

若要改測試資料，集中修改 `fixtures/api-mocks.ts`；若 UI 結構調整，優先更新
`pages/` 內的 Page Objects，避免在 spec 中散落 selector。
