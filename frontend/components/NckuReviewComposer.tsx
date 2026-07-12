"use client";

import { FormEvent, useState } from "react";
import type { ReactNode } from "react";
import { LockKeyhole, LogIn, Send, ShieldCheck } from "lucide-react";

import { USER_ROLES, useAppContext } from "@/components/AppContext";
import { getPublicApiBaseUrl } from "@/lib/public-runtime-config";

const API_BASE_URL = getPublicApiBaseUrl();
const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;

type ReviewType = "rental_warning" | "rental_recommendation" | "food_recommendation" | "protein_meal_prep" | "other";

export function NckuReviewComposer() {
  const { supabase, user, userRole, isAuthLoading } = useAppContext();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [reviewType, setReviewType] = useState<ReviewType>("rental_warning");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [locationName, setLocationName] = useState("");
  const [area, setArea] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [authMessage, setAuthMessage] = useState<string | null>(null);
  const [isAuthActionLoading, setIsAuthActionLoading] = useState(false);

  const email = user?.email?.toLowerCase() ?? "";
  const isNckuUser =
    userRole === USER_ROLES.NCKU_VERIFIED ||
    userRole === USER_ROLES.ADMIN;
  const canSubmit = Boolean(supabase && user && isNckuUser && title.trim() && content.trim() && !isSubmitting);

  async function signInWithGoogle() {
    console.log("[NCKUall Auth] Google login button clicked.");

    if (!supabase) {
      console.error(
        "[NCKUall Auth] Supabase client is not available.",
        {
          hasSupabaseUrl: Boolean(SUPABASE_URL),
          hasSupabaseAnonKey: Boolean(
            process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
          ),
        },
      );
      setAuthMessage("Supabase Auth 尚未設定，請檢查前端環境變數。");
      return;
    }

    setAuthMessage(null);
    setIsAuthActionLoading(true);
    try {
      const { data, error } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo: `${window.location.origin}/auth/callback`,
          skipBrowserRedirect: true,
        },
      });
      if (error) {
        console.error("[NCKUall Auth] Google OAuth failed to start.", error);
        setAuthMessage(`Google 登入啟動失敗：${error.message}`);
        return;
      }

      if (data.url) {
        console.log("[NCKUall Auth] Redirecting to Google OAuth.");
        window.location.assign(data.url);
        return;
      }

      console.error("[NCKUall Auth] Google OAuth returned no redirect URL.");
      setAuthMessage("Google 登入啟動失敗：Supabase 沒有回傳登入網址。");
    } catch (error) {
      console.error("[NCKUall Auth] Unexpected Google login error.", error);
      setAuthMessage("Google 登入啟動失敗，請稍後再試。");
    } finally {
      setIsAuthActionLoading(false);
    }
  }

  async function signOut() {
    if (!supabase) {
      setAuthMessage("Supabase Auth 尚未設定，無法登出。");
      return;
    }

    setAuthMessage(null);
    setIsAuthActionLoading(true);
    try {
      const { error } = await supabase.auth.signOut();
      if (error) {
        setAuthMessage(`登出失敗：${error.message}`);
      }
    } catch {
      setAuthMessage("登出失敗，請稍後再試。");
    } finally {
      setIsAuthActionLoading(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit || !supabase) {
      return;
    }

    setIsSubmitting(true);
    setStatusMessage(null);

    try {
      const { data } = await supabase.auth.getSession();
      const accessToken = data.session?.access_token;
      if (!accessToken) {
        setStatusMessage("登入狀態已過期，請重新登入。");
        return;
      }

      const response = await fetch(`${API_BASE_URL}/api/life/reviews`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          review_type: reviewType,
          title,
          content,
          location_name: locationName || null,
          area: area || null,
          author_alias: email.split("@", 1)[0],
          tags: buildTags(reviewType),
          metadata: {
            auth_provider: "supabase_google",
          },
        }),
      });

      if (!response.ok) {
        const error = await response.json().catch(() => null);
        setStatusMessage(error?.detail ?? "送出失敗，請稍後再試。");
        return;
      }

      setTitle("");
      setContent("");
      setLocationName("");
      setArea("");
      setStatusMessage("已送出，謝謝你幫大家把資訊變得更可靠。");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <section className="mt-6 rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-sm font-semibold text-campus">學生分享</p>
          <h2 className="mt-1 text-lg font-bold tracking-normal text-ink dark:text-slate-100">發布生活評價</h2>
          <p className="mt-1 text-sm leading-6 text-slate-600 dark:text-slate-300">
            租屋避雷、美食推薦、高蛋白備餐食材地點，都可以在這裡補上。
          </p>
        </div>

        <AuthButton
          email={email}
          isAuthLoading={isAuthLoading}
          isAuthActionLoading={isAuthActionLoading}
          onSignIn={signInWithGoogle}
          onSignOut={signOut}
        />
      </div>

      {authMessage && (
        <Notice
          icon={<LockKeyhole className="h-4 w-4" />}
          text={authMessage}
          tone="warning"
        />
      )}

      {!supabase && (
        <Notice
          icon={<LockKeyhole className="h-4 w-4" />}
          text="Supabase Auth 尚未設定，請先提供 NEXT_PUBLIC_SUPABASE_URL 與 NEXT_PUBLIC_SUPABASE_ANON_KEY。"
          tone="warning"
        />
      )}

      {!user && !isAuthLoading && (
        <Notice icon={<LogIn className="h-4 w-4" />} text="請登入以發布評價" tone="neutral" />
      )}

      {user && !isNckuUser && (
        <Notice
          icon={<LockKeyhole className="h-4 w-4" />}
          text="僅限成大教職員工生 (@ncku.edu.tw 或 @gs.ncku.edu.tw) 發布評價，以維持資訊真實性"
          tone="warning"
        />
      )}

      {user && isNckuUser && (
        <Notice icon={<ShieldCheck className="h-4 w-4" />} text="已通過成大信箱認證，可以發布評價。" tone="success" />
      )}

      <form className="mt-4 grid gap-3" onSubmit={handleSubmit}>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
            類型
            <select
              value={reviewType}
              onChange={(event) => setReviewType(event.target.value as ReviewType)}
              disabled={!user || !isNckuUser}
              className="mt-2 h-11 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-ink outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 disabled:bg-slate-100 disabled:text-slate-400 dark:disabled:bg-slate-800"
            >
              <option value="rental_warning">租屋避雷</option>
              <option value="rental_recommendation">租屋推薦</option>
              <option value="food_recommendation">周邊美食</option>
              <option value="protein_meal_prep">高蛋白備餐</option>
              <option value="other">其他</option>
            </select>
          </label>

          <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
            區域
            <input
              value={area}
              onChange={(event) => setArea(event.target.value)}
              disabled={!user || !isNckuUser}
              placeholder="例如：勝利校區、東寧路"
              className="mt-2 h-11 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-ink outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 disabled:bg-slate-100 disabled:text-slate-400 dark:disabled:bg-slate-800"
            />
          </label>
        </div>

        <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
          地點或標的
          <input
            value={locationName}
            onChange={(event) => setLocationName(event.target.value)}
            disabled={!user || !isNckuUser}
            placeholder="例如：某租屋處、某超市"
            className="mt-2 h-11 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-ink outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 disabled:bg-slate-100 disabled:text-slate-400 dark:disabled:bg-slate-800"
          />
        </label>

        <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
          標題
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            disabled={!user || !isNckuUser}
            placeholder="用一句話說重點"
            className="mt-2 h-11 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-ink outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 disabled:bg-slate-100 disabled:text-slate-400 dark:disabled:bg-slate-800"
          />
        </label>

        <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
          內容
          <textarea
            value={content}
            onChange={(event) => setContent(event.target.value)}
            disabled={!user || !isNckuUser}
            placeholder="請描述實際經驗、時間點、注意事項。避免公開個資。"
            rows={4}
            className="mt-2 w-full resize-none rounded-md border border-slate-300 bg-white px-3 py-2 text-sm leading-6 text-ink outline-none dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 disabled:bg-slate-100 disabled:text-slate-400 dark:disabled:bg-slate-800"
          />
        </label>

        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          {statusMessage && <p className="text-sm text-slate-600 dark:text-slate-300">{statusMessage}</p>}
          <button
            type="submit"
            disabled={!canSubmit}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-campus px-4 text-sm font-semibold text-white transition disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            <Send className="h-4 w-4" />
            發布評價
          </button>
        </div>
      </form>
    </section>
  );
}

function AuthButton({
  email,
  isAuthLoading,
  isAuthActionLoading,
  onSignIn,
  onSignOut,
}: {
  email: string;
  isAuthLoading: boolean;
  isAuthActionLoading: boolean;
  onSignIn: () => Promise<void>;
  onSignOut: () => Promise<void>;
}) {
  if (isAuthLoading) {
    return <span className="text-sm text-slate-500">登入狀態確認中...</span>;
  }

  if (email) {
    return (
      <button
        type="button"
        onClick={onSignOut}
        disabled={isAuthActionLoading}
        className="h-10 rounded-md border border-slate-200 bg-white px-3 text-sm font-medium text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-300 dark:hover:bg-slate-800"
      >
        {isAuthActionLoading ? "處理中..." : `登出 ${email}`}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={onSignIn}
      disabled={isAuthActionLoading}
      className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-campus px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
    >
      <LogIn className="h-4 w-4" />
      {isAuthActionLoading ? "登入中..." : "使用 Google 登入"}
    </button>
  );
}

function Notice({ icon, text, tone }: { icon: ReactNode; text: string; tone: "neutral" | "warning" | "success" }) {
  const styles = {
    neutral: "border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300",
    warning: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200",
    success: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
  };

  return (
    <div className={`mt-4 flex items-start gap-2 rounded-md border px-3 py-2 text-sm leading-6 ${styles[tone]}`}>
      <span className="mt-1 shrink-0">{icon}</span>
      <span>{text}</span>
    </div>
  );
}

function buildTags(reviewType: ReviewType) {
  const tags: Record<ReviewType, string[]> = {
    rental_warning: ["租屋", "避雷"],
    rental_recommendation: ["租屋", "推薦"],
    food_recommendation: ["美食", "推薦"],
    protein_meal_prep: ["高蛋白", "備餐"],
    other: ["生活"],
  };
  return tags[reviewType];
}
