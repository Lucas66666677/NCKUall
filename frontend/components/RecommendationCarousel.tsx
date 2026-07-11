"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  BriefcaseBusiness,
  LibraryBig,
  LockKeyhole,
  Sparkles,
} from "lucide-react";

import { useAppContext } from "@/components/AppContext";
import type {
  RecommendationItem,
  RecommendationResponse,
} from "@/lib/api-types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

const resourceLabels = {
  course: "課程",
  career: "職涯",
} as const;

export function RecommendationCarousel() {
  const {
    currentDepartment,
    isAuthLoading,
    user,
    supabase,
  } = useAppContext();
  const [recommendations, setRecommendations] =
    useState<RecommendationResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [hasError, setHasError] = useState(false);

  useEffect(() => {
    if (isAuthLoading || !user || !supabase) {
      setRecommendations(null);
      setHasError(false);
      setIsLoading(false);
      return;
    }

    const supabaseClient = supabase;
    const controller = new AbortController();
    let isMounted = true;

    async function loadRecommendations() {
      setIsLoading(true);
      setHasError(false);
      try {
        const {
          data: { session },
        } = await supabaseClient.auth.getSession();
        const token = session?.access_token;
        if (!token) {
          throw new Error("Missing access token");
        }

        const params = new URLSearchParams({
          limit: "6",
        });
        if (currentDepartment.id) {
          params.set("department_id", currentDepartment.id);
        }

        const response = await fetch(
          `${API_BASE_URL}/api/recommendations?${params.toString()}`,
          {
            headers: {
              Authorization: `Bearer ${token}`,
              Accept: "application/json",
            },
            signal: controller.signal,
          },
        );
        if (!response.ok) {
          throw new Error(`Recommendations API failed with ${response.status}`);
        }
        const payload = (await response.json()) as RecommendationResponse;
        if (isMounted) {
          setRecommendations(payload);
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        if (isMounted) {
          setHasError(true);
        }
      } finally {
        if (isMounted && !controller.signal.aborted) {
          setIsLoading(false);
        }
      }
    }

    void loadRecommendations();
    return () => {
      isMounted = false;
      controller.abort();
    };
  }, [currentDepartment.id, isAuthLoading, supabase, user]);

  const items = useMemo(
    () => recommendations?.items ?? [],
    [recommendations],
  );

  async function recordRecommendationView(item: RecommendationItem) {
    if (!supabase || !user) {
      return;
    }
    try {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      const token = session?.access_token;
      if (!token) {
        return;
      }
      await fetch(`${API_BASE_URL}/api/recommendations/views`, {
        method: "POST",
        keepalive: true,
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          resource_type: item.resource_type,
          resource_id: item.resource_id,
        }),
      });
    } catch {
      // Recommendation telemetry must never interrupt navigation.
    }
  }

  if (isAuthLoading) {
    return <RecommendationSkeleton />;
  }

  if (!user) {
    return (
      <section className="mt-6 rounded-lg border border-slate-200 bg-white/85 p-4 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-900/85">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-300">
            <LockKeyhole className="h-5 w-5" aria-hidden="true" />
          </span>
          <div>
            <h2 className="text-base font-bold text-ink dark:text-slate-100">
              專屬推薦
            </h2>
            <p className="mt-1 text-sm leading-6 text-slate-600 dark:text-slate-300">
              登入後會依你的課程與職涯瀏覽紀錄生成個人化 Feed。
            </p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white/90 shadow-sm backdrop-blur-md dark:border-slate-800 dark:bg-slate-900/90">
      <div className="flex flex-col gap-3 border-b border-slate-100 px-4 py-4 dark:border-slate-800 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-campus">
            <Sparkles className="h-5 w-5" aria-hidden="true" />
            <h2 className="text-lg font-bold text-ink dark:text-slate-100">
              猜你喜歡
            </h2>
          </div>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {recommendations?.profile_ready
              ? `依最近 ${recommendations.based_on_count} 筆語意訊號推薦`
              : `目前科系：${currentDepartment.name}`}
          </p>
        </div>
        <span className="w-fit rounded-md bg-mist px-2.5 py-1 text-xs font-semibold text-campus dark:bg-slate-800 dark:text-teal-300">
          個人化 pgvector Feed
        </span>
      </div>

      {isLoading && <RecommendationSkeletonBody />}

      {!isLoading && hasError && (
        <p className="px-4 py-10 text-center text-sm leading-6 text-slate-500 dark:text-slate-400">
          目前無法取得專屬推薦。
        </p>
      )}

      {!isLoading && !hasError && items.length === 0 && (
        <p className="px-4 py-10 text-center text-sm leading-6 text-slate-500 dark:text-slate-400">
          瀏覽幾堂課或實驗室資料後，這裡會開始出現你的專屬推薦。
        </p>
      )}

      {!isLoading && !hasError && items.length > 0 && (
        <div className="flex snap-x gap-3 overflow-x-auto px-4 py-4 [scrollbar-width:thin]">
          {items.map((item) => (
            <Link
              key={`${item.resource_type}-${item.resource_id}`}
              href={item.href}
              onClick={() => {
                void recordRecommendationView(item);
              }}
              className="group flex min-h-[210px] w-[260px] shrink-0 snap-start flex-col justify-between rounded-lg border border-slate-200 bg-white p-4 shadow-sm transition hover:-translate-y-0.5 hover:border-campus/50 hover:shadow-md dark:border-slate-800 dark:bg-slate-950 dark:hover:border-teal-500/60 sm:w-[300px]"
            >
              <div>
                <div className="flex items-center justify-between gap-2">
                  <span className="inline-flex items-center gap-1.5 rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {item.resource_type === "course" ? (
                      <LibraryBig className="h-3.5 w-3.5" aria-hidden="true" />
                    ) : (
                      <BriefcaseBusiness className="h-3.5 w-3.5" aria-hidden="true" />
                    )}
                    {resourceLabels[item.resource_type]}
                  </span>
                  <span className="text-xs font-semibold text-campus dark:text-teal-300">
                    {Math.round(item.adjusted_score * 100)}%
                  </span>
                </div>

                <h3 className="mt-4 line-clamp-2 text-base font-bold leading-6 text-ink group-hover:text-campus dark:text-slate-100 dark:group-hover:text-teal-300">
                  {item.title}
                </h3>
                <p className="mt-1 truncate text-sm text-slate-500 dark:text-slate-400">
                  {item.subtitle ?? item.department_name ?? "成大資源"}
                </p>
                <p className="mt-3 line-clamp-3 text-sm leading-6 text-slate-600 dark:text-slate-300">
                  {item.reason}
                </p>
              </div>

              <div className="mt-4">
                <div className="mb-3 flex flex-wrap gap-1.5">
                  {(item.tags.length > 0
                    ? item.tags
                    : [item.department_name ?? "NCKUall"]
                  )
                    .slice(0, 3)
                    .map((tag) => (
                      <span
                        key={tag}
                        className="rounded-md bg-mist px-2 py-1 text-[11px] font-medium text-campus dark:bg-slate-800 dark:text-teal-300"
                      >
                        {tag}
                      </span>
                    ))}
                </div>
                <span className="inline-flex items-center gap-1 text-sm font-semibold text-campus dark:text-teal-300">
                  查看推薦
                  <ArrowRight className="h-4 w-4 transition group-hover:translate-x-0.5" aria-hidden="true" />
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

function RecommendationSkeleton() {
  return (
    <section className="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="border-b border-slate-100 px-4 py-4 dark:border-slate-800">
        <div className="h-5 w-28 animate-pulse rounded bg-slate-200 dark:bg-slate-700" />
        <div className="mt-2 h-3 w-56 animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
      </div>
      <RecommendationSkeletonBody />
    </section>
  );
}

function RecommendationSkeletonBody() {
  return (
    <div className="flex gap-3 overflow-hidden px-4 py-4">
      {Array.from({ length: 3 }, (_, index) => (
        <div
          key={index}
          className="h-[210px] w-[260px] shrink-0 animate-pulse rounded-lg border border-slate-200 bg-slate-50 p-4 dark:border-slate-800 dark:bg-slate-950 sm:w-[300px]"
        >
          <div className="h-6 w-20 rounded-md bg-slate-200 dark:bg-slate-700" />
          <div className="mt-5 h-5 w-3/4 rounded bg-slate-200 dark:bg-slate-700" />
          <div className="mt-2 h-4 w-1/2 rounded bg-slate-100 dark:bg-slate-800" />
          <div className="mt-5 space-y-2">
            <div className="h-3 w-full rounded bg-slate-100 dark:bg-slate-800" />
            <div className="h-3 w-5/6 rounded bg-slate-100 dark:bg-slate-800" />
            <div className="h-3 w-2/3 rounded bg-slate-100 dark:bg-slate-800" />
          </div>
        </div>
      ))}
    </div>
  );
}
