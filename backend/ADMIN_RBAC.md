# Supabase 管理員權限設定

後端只信任經 Supabase 簽署 JWT 中的 `app_metadata.is_admin`，不信任使用者可
自行修改的 `user_metadata`。管理員標籤必須透過可信任的伺服器環境設定。

## 指派管理員

以下程式只能在本機管理腳本、CI secret job 或受保護的後端執行：

```ts
import { createClient } from "@supabase/supabase-js";

const supabaseAdmin = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!,
  { auth: { autoRefreshToken: false, persistSession: false } },
);

const userId = "SUPABASE_AUTH_USER_UUID";
const { error } = await supabaseAdmin.auth.admin.updateUserById(userId, {
  app_metadata: { is_admin: true },
});

if (error) throw error;
```

撤銷權限時將值設為 `false`：

```ts
await supabaseAdmin.auth.admin.updateUserById(userId, {
  app_metadata: { is_admin: false },
});
```

標籤變更後，該使用者需重新登入或呼叫 `supabase.auth.refreshSession()`，讓新的
access token 帶入更新後的 `app_metadata`。

## 金鑰邊界

- `SUPABASE_SERVICE_ROLE_KEY` 僅供管理腳本或可信任後端使用。
- 絕對不可使用 `NEXT_PUBLIC_` 前綴，也不可放入 Vercel 的瀏覽器端 bundle。
- FastAPI 驗證一般請求只需 `SUPABASE_JWT_SECRET` 與
  `SUPABASE_JWT_AUDIENCE`，不需要 service role key。
- 前端的 `ADMIN` 判斷只控制 UI；真正的安全邊界是 FastAPI
  `verify_admin_user` dependency。
