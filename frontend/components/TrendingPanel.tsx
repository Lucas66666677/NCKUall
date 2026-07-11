"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  CalendarDays,
  FlaskConical,
  LibraryBig,
  TrendingUp,
} from "lucide-react";


const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

type TrendingResourceType = "course" | "lab" | "event";
type TrendingCollection = "courses" | "labs" | "events";

type TrendingItem = {
  resource_type: TrendingResourceType;
  resource_id: string;
  title: string;
  subtitle: string | null;
  interaction_count: number;
  href: string;
};

type TrendingResponse = {
  window_hours: number;
  courses: TrendingItem[];
  labs: TrendingItem[];
  events: TrendingItem[];
};

const categories: Array<{
  id: TrendingCollection;
  label: string;
  icon: typeof LibraryBig;
}> = [
  { id: "courses", label: "課程", icon: LibraryBig },
  { id: "labs", label: "實驗室", icon: FlaskConical },
  { id: "events", label: "活動", icon: CalendarDays },
];

export function TrendingPanel() {
  const [activeCategory, setActiveCategory] =
    useState<TrendingCollection>("courses");
  const [trending, setTrending] = useState<TrendingResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();

    async function loadTrending() {
      setIsLoading(true);
      setHasError(false);
      try {
        const response = await fetch(
          `${API_BASE_URL}/api/analytics/trending`,
          { signal: controller.signal },
        );
        if (!response.ok) {
          throw new Error(`Trending API failed with ${response.status}`);
        }
        setTrending((await response.json()) as TrendingResponse);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setHasError(true);
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      }
    }

    void loadTrending();
    return () => controller.abort();
  }, []);

  const items = useMemo(
    () => trending?.[activeCategory] ?? [],
    [activeCategory, trending],
  );

  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="border-b border-slate-100 px-4 py-4 dark:border-slate-800">
        <div className="flex items-center gap-2 text-campus">
          <TrendingUp className="h-5 w-5" aria-hidden="true" />
          <h2 className="text-base font-bold text-ink dark:text-slate-100">成大熱門觀測站</h2>
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          最近 {trending?.window_hours ?? 74} 小時匿名關注趨勢
        </p>
      </div>

      <div
        className="grid grid-cols-3 border-b border-slate-100 p-1.5 dark:border-slate-800"
        role="tablist"
        aria-label="熱門資源分類"
      >
        {categories.map((category) => {
          const Icon = category.icon;
          const isActive = activeCategory === category.id;
          return (
            <button
              key={category.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => setActiveCategory(category.id)}
              className={`flex h-9 items-center justify-center gap-1.5 rounded-md text-xs font-semibold transition ${
                isActive
                  ? "bg-ink text-white dark:bg-teal-700"
                  : "text-slate-500 hover:bg-slate-100 hover:text-ink dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-white"
              }`}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              {category.label}
            </button>
          );
        })}
      </div>

      <div className="min-h-[330px]">
        {isLoading && <TrendingSkeleton />}

        {!isLoading && hasError && (
          <p className="px-4 py-10 text-center text-sm leading-6 text-slate-500 dark:text-slate-400">
            目前無法取得熱門趨勢。
          </p>
        )}

        {!isLoading && !hasError && items.length === 0 && (
          <p className="px-4 py-10 text-center text-sm leading-6 text-slate-500 dark:text-slate-400">
            近 74 小時尚無足夠資料，熱門榜正在累積中。
          </p>
        )}

        {!isLoading && !hasError && items.length > 0 && (
          <ol className="divide-y divide-slate-100 dark:divide-slate-800">
            {items.map((item, index) => (
              <li key={`${item.resource_type}-${item.resource_id}`}>
                <Link
                  href={item.href}
                  className="group flex min-h-[66px] gap-3 px-4 py-3 transition hover:bg-slate-50 dark:hover:bg-slate-800/70"
                >
                  <span
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-bold ${
                      index === 0
                        ? "bg-brick text-white"
                        : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
                    }`}
                  >
                    {index + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-ink group-hover:text-campus dark:text-slate-100 dark:group-hover:text-teal-300">
                      {item.title}
                    </span>
                    <span className="mt-1 flex items-center justify-between gap-2 text-xs text-slate-500 dark:text-slate-400">
                      <span className="truncate">
                        {item.subtitle ?? "成大校園資源"}
                      </span>
                      <span className="shrink-0">
                        {item.interaction_count} 次關注
                      </span>
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ol>
        )}
      </div>

      <p className="border-t border-slate-100 px-4 py-3 text-[11px] leading-5 text-slate-400 dark:border-slate-800 dark:text-slate-500">
        僅統計匿名資源事件，不記錄帳號、IP 或搜尋文字。
      </p>
    </section>
  );
}

function TrendingSkeleton() {
  return (
    <div className="divide-y divide-slate-100 dark:divide-slate-800" aria-label="熱門趨勢載入中">
      {Array.from({ length: 5 }, (_, index) => (
        <div
          key={index}
          className="flex min-h-[66px] animate-pulse gap-3 px-4 py-3"
        >
          <div className="h-6 w-6 rounded-md bg-slate-200 dark:bg-slate-700" />
          <div className="flex-1">
            <div className="h-4 w-3/4 rounded bg-slate-200 dark:bg-slate-700" />
            <div className="mt-2 h-3 w-1/2 rounded bg-slate-100 dark:bg-slate-800" />
          </div>
        </div>
      ))}
    </div>
  );
}
