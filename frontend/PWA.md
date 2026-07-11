# NCKUall PWA

本專案使用 App Router 原生 Manifest 與 `@serwist/next`。Service Worker 只在
production build 啟用，避免開發環境被舊快取干擾。

## 快取策略

| 資源 | 策略 | 保存上限 |
| --- | --- | --- |
| `/_next/static`、CSS、字體、腳本、PWA icons | Cache First | 160 筆／365 天 |
| 圖片 | Cache First | 80 筆／30 天 |
| 公開 GET API | Stale While Revalidate | 100 筆／7 天 |
| 頁面導覽 | Network First，4 秒後使用快取 | 32 筆／7 天 |

公開 API 白名單：

- `/api/courses`
- `/api/careers`
- `/api/events`
- `/api/life`
- `/api/departments`
- `/api/analytics`

`POST`、`PUT`、`/api/chat`、`/api/admin` 與 Supabase Auth 不會進入 API
runtime cache。查詢參數會成為 cache key 的一部分，因此不同科系的課程清單不會
互相覆蓋。

## Build 與本機測試

```bash
npm run build
npm run start
```

PWA 必須透過 HTTPS 或瀏覽器認可的 localhost origin 使用。開啟 Chrome DevTools：

1. 在 Application > Manifest 確認 `display: standalone` 與 192/512 icons。
2. 在 Application > Service Workers 確認 `/sw.js` scope 為 `/`。
3. 先在線瀏覽課程與評價，再勾選 Offline 並重新整理，確認快取資料仍可呈現。
4. 在 Network > Offline 測試頂端離線 Toast。

## 部署

- Vercel 必須部署 `npm run build` 的 production 版本。
- `NEXT_PUBLIC_API_BASE_URL` 必須是 HTTPS，且 FastAPI CORS 允許正式前端 origin。
- `/sw.js` 使用 `must-revalidate`，避免 CDN 長時間保留舊版 Service Worker。
- 每次調整 cache schema 時遞增 `app/sw.ts` 內的 cache name 版本，例如由
  `nckuall-public-api-v1` 改為 `v2`。
