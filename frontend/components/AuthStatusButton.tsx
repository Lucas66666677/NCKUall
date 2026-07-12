"use client";

import { useState } from "react";
import { Loader2, LogIn, LogOut } from "lucide-react";

import { useAppContext } from "@/components/AppContext";

export function AuthStatusButton({
  compact = false,
}: {
  compact?: boolean;
}) {
  const { supabase, user, isAuthLoading } = useAppContext();
  const [isBusy, setIsBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function signInWithGoogle() {
    setMessage(null);
    if (!supabase) {
      setMessage("登入尚未設定");
      console.error(
        "[NCKUall Auth] Missing Supabase client. Check NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY.",
      );
      return;
    }

    setIsBusy(true);
    try {
      const { data, error } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo: `${window.location.origin}/auth/callback`,
          skipBrowserRedirect: true,
        },
      });

      if (error) {
        setMessage("登入啟動失敗");
        console.error("[NCKUall Auth] Google OAuth failed.", error);
        return;
      }

      if (!data.url) {
        setMessage("登入網址不存在");
        console.error("[NCKUall Auth] Supabase returned no OAuth URL.");
        return;
      }

      window.location.assign(data.url);
    } catch (error) {
      setMessage("登入失敗");
      console.error("[NCKUall Auth] Unexpected OAuth error.", error);
    } finally {
      setIsBusy(false);
    }
  }

  async function signOut() {
    setMessage(null);
    if (!supabase) {
      setMessage("登入尚未設定");
      return;
    }

    setIsBusy(true);
    try {
      const { error } = await supabase.auth.signOut();
      if (error) {
        setMessage("登出失敗");
        console.error("[NCKUall Auth] Sign out failed.", error);
      }
    } catch (error) {
      setMessage("登出失敗");
      console.error("[NCKUall Auth] Unexpected sign out error.", error);
    } finally {
      setIsBusy(false);
    }
  }

  const buttonClassName =
    "inline-flex h-10 items-center justify-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800";

  if (isAuthLoading) {
    return (
      <span className={buttonClassName} aria-live="polite">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        {!compact && "登入確認中"}
      </span>
    );
  }

  if (user) {
    return (
      <div className="relative">
        <button
          type="button"
          onClick={signOut}
          disabled={isBusy}
          className={buttonClassName}
          aria-label={`登出 ${user.email ?? "目前帳號"}`}
          title={user.email ?? "已登入"}
        >
          {isBusy ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <LogOut className="h-4 w-4" aria-hidden="true" />
          )}
          {!compact && (
            <span className="max-w-36 truncate">
              {user.email ?? "已登入"}
            </span>
          )}
        </button>
        {message && (
          <p className="absolute right-0 top-12 w-44 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800 shadow-sm dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
            {message}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={signInWithGoogle}
        disabled={isBusy}
        className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-campus px-3 text-sm font-semibold text-white shadow-sm transition hover:bg-campus/90 disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        {isBusy ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <LogIn className="h-4 w-4" aria-hidden="true" />
        )}
        {!compact && "Google 登入"}
      </button>
      {message && (
        <p className="absolute right-0 top-12 w-52 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800 shadow-sm dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          {message}
        </p>
      )}
    </div>
  );
}
