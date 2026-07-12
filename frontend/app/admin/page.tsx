"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  EyeOff,
  Loader2,
  MessageSquareWarning,
  Search,
  ShieldCheck,
} from "lucide-react";

import { USER_ROLES, useAppContext } from "@/components/AppContext";
import { getPublicApiBaseUrl } from "@/lib/public-runtime-config";


const API_BASE_URL = getPublicApiBaseUrl();
const PAGE_SIZE = 10;

type ModerationStatus = "APPROVED" | "HIDDEN" | "PENDING";

type FlaggedReview = {
  id: string;
  review_type: string;
  title: string;
  content: string;
  area: string | null;
  author_alias: string | null;
  rating: number | null;
  report_count: number;
  moderation_status: ModerationStatus;
  last_reported_at: string | null;
  created_at: string;
};

type FlaggedReviewsResponse = {
  items: FlaggedReview[];
  total: number;
  limit: number;
  offset: number;
};

type DashboardStats = {
  today_new_reviews: number;
  pending_flagged_reviews: number;
  popular_search_terms: string[];
};

const emptyStats: DashboardStats = {
  today_new_reviews: 0,
  pending_flagged_reviews: 0,
  popular_search_terms: [],
};

export default function AdminPage() {
  const { userRole, user, supabase, isAuthLoading } = useAppContext();
  const [reviews, setReviews] = useState<FlaggedReview[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<DashboardStats>(emptyStats);
  const [page, setPage] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [updatingReviewId, setUpdatingReviewId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const isAdmin = userRole === USER_ROLES.ADMIN;

  const getAccessToken = useCallback(async () => {
    if (!supabase) {
      return null;
    }
    const { data } = await supabase.auth.getSession();
    return data.session?.access_token ?? null;
  }, [supabase]);

  const loadDashboard = useCallback(async () => {
    if (!isAdmin) {
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setErrorMessage(null);
    try {
      const token = await getAccessToken();
      if (!token) {
        throw new Error("missing_session");
      }
      const headers = { Authorization: `Bearer ${token}` };
      const offset = page * PAGE_SIZE;
      const [reviewsResponse, statsResponse] = await Promise.all([
        fetch(
          `${API_BASE_URL}/api/admin/reviews/flagged?limit=${PAGE_SIZE}&offset=${offset}`,
          { headers },
        ),
        fetch(`${API_BASE_URL}/api/admin/stats`, { headers }),
      ]);

      if (!reviewsResponse.ok || !statsResponse.ok) {
        throw new Error("admin_api_failed");
      }

      const reviewData =
        (await reviewsResponse.json()) as FlaggedReviewsResponse;
      const statsData = (await statsResponse.json()) as DashboardStats;
      setReviews(reviewData.items);
      setTotal(reviewData.total);
      setStats(statsData);

      if (reviewData.items.length === 0 && page > 0) {
        setPage((current) => current - 1);
      }
    } catch {
      setErrorMessage("無法載入管理資料，請重新登入或稍後再試。");
    } finally {
      setIsLoading(false);
    }
  }, [getAccessToken, isAdmin, page]);

  useEffect(() => {
    if (!isAuthLoading) {
      void loadDashboard();
    }
  }, [isAuthLoading, loadDashboard]);

  async function updateStatus(
    reviewId: string,
    status: ModerationStatus,
  ) {
    setUpdatingReviewId(reviewId);
    setErrorMessage(null);
    try {
      const token = await getAccessToken();
      if (!token) {
        throw new Error("missing_session");
      }
      const response = await fetch(
        `${API_BASE_URL}/api/admin/reviews/${reviewId}/status`,
        {
          method: "PUT",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ status }),
        },
      );
      if (!response.ok) {
        throw new Error(`update_failed_${response.status}`);
      }
      await loadDashboard();
    } catch {
      setErrorMessage("狀態更新失敗，資料未變更。");
    } finally {
      setUpdatingReviewId(null);
    }
  }

  if (isAuthLoading) {
    return <AdminLoading />;
  }

  if (!isAdmin) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-mist px-4">
        <section className="w-full max-w-lg rounded-lg border border-amber-200 bg-white p-6 shadow-sm">
          <AlertTriangle className="h-8 w-8 text-amber-600" />
          <h1 className="mt-4 text-xl font-bold text-ink">無管理員權限</h1>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            此頁面僅供具備受信任管理員標籤的帳號使用。你目前登入的帳號為
            <span className="font-semibold text-ink">
              {user?.email ? ` ${user.email}` : " 遊客"}
            </span>
            。
          </p>
        </section>
      </main>
    );
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <main className="min-h-screen bg-mist pb-12">
      <header className="border-b border-white/70 bg-white/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-5 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-campus">
              <ShieldCheck className="h-4 w-4" />
              Admin Console
            </div>
            <h1 className="mt-1 text-2xl font-bold tracking-normal text-ink">
              內容審核中心
            </h1>
          </div>
          <p className="text-sm text-slate-500">{user?.email}</p>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <section
          className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3"
          aria-label="營運摘要"
        >
          <Metric
            icon={<BarChart3 className="h-5 w-5" />}
            label="今日新增評價數"
            value={stats.today_new_reviews.toString()}
          />
          <Metric
            icon={<MessageSquareWarning className="h-5 w-5" />}
            label="待審核檢舉數"
            value={stats.pending_flagged_reviews.toString()}
            emphasis
          />
          <Metric
            icon={<Search className="h-5 w-5" />}
            label="最熱門搜尋詞"
            value={stats.popular_search_terms[0] ?? "尚無搜尋資料"}
          />
        </section>

        {stats.popular_search_terms.length > 1 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {stats.popular_search_terms.slice(1).map((term) => (
              <span
                key={term}
                className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-500"
              >
                {term}
              </span>
            ))}
          </div>
        )}

        {errorMessage && (
          <div className="mt-5 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {errorMessage}
          </div>
        )}

        <section className="mt-6" aria-labelledby="review-queue-title">
          <div className="mb-3 flex items-end justify-between gap-3">
            <div>
              <h2
                id="review-queue-title"
                className="text-lg font-bold text-ink"
              >
                評論審核隊列
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                共 {total} 筆尚未處理的學生檢舉
              </p>
            </div>
            {isLoading && <Loader2 className="h-5 w-5 animate-spin text-campus" />}
          </div>

          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="overflow-x-auto">
              <table className="min-w-[940px] w-full border-collapse text-left">
                <thead className="bg-slate-50 text-xs font-semibold text-slate-500">
                  <tr>
                    <th className="px-4 py-3">評論</th>
                    <th className="px-4 py-3">作者／區域</th>
                    <th className="px-4 py-3">檢舉</th>
                    <th className="px-4 py-3">狀態</th>
                    <th className="px-4 py-3 text-right">審核操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {!isLoading && reviews.length === 0 && (
                    <tr>
                      <td
                        colSpan={5}
                        className="px-4 py-12 text-center text-sm text-slate-500"
                      >
                        目前沒有待處理的檢舉。
                      </td>
                    </tr>
                  )}
                  {reviews.map((review) => {
                    const isUpdating = updatingReviewId === review.id;
                    return (
                      <tr key={review.id} className="align-top">
                        <td className="max-w-md px-4 py-4">
                          <p className="font-semibold text-ink">{review.title}</p>
                          <p className="mt-1 line-clamp-2 text-sm leading-6 text-slate-600">
                            {review.content}
                          </p>
                        </td>
                        <td className="px-4 py-4 text-sm text-slate-600">
                          <p>@{review.author_alias ?? "匿名同學"}</p>
                          <p className="mt-1 text-xs text-slate-400">
                            {review.area ?? "未填區域"}
                          </p>
                        </td>
                        <td className="px-4 py-4">
                          <span className="inline-flex rounded-md bg-amber-50 px-2 py-1 text-xs font-semibold text-amber-700">
                            {review.report_count} 次
                          </span>
                        </td>
                        <td className="px-4 py-4">
                          <StatusBadge status={review.moderation_status} />
                        </td>
                        <td className="px-4 py-4">
                          <div className="flex justify-end gap-2">
                            <button
                              type="button"
                              disabled={isUpdating}
                              onClick={() =>
                                void updateStatus(review.id, "APPROVED")
                              }
                              className="inline-flex h-9 items-center gap-1.5 rounded-md border border-emerald-200 px-3 text-xs font-semibold text-emerald-700 hover:bg-emerald-50 disabled:opacity-50"
                            >
                              <CheckCircle2 className="h-4 w-4" />
                              駁回檢舉
                            </button>
                            <button
                              type="button"
                              disabled={isUpdating}
                              onClick={() =>
                                void updateStatus(review.id, "HIDDEN")
                              }
                              className="inline-flex h-9 items-center gap-1.5 rounded-md bg-red-600 px-3 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-50"
                            >
                              {isUpdating ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                              ) : (
                                <EyeOff className="h-4 w-4" />
                              )}
                              隱藏評論
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3">
              <p className="text-xs text-slate-500">
                第 {page + 1} / {pageCount} 頁
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={page === 0 || isLoading}
                  onClick={() => setPage((current) => current - 1)}
                  className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 text-slate-600 disabled:opacity-40"
                  aria-label="上一頁"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <button
                  type="button"
                  disabled={page + 1 >= pageCount || isLoading}
                  onClick={() => setPage((current) => current + 1)}
                  className="flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 text-slate-600 disabled:opacity-40"
                  aria-label="下一頁"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}

function Metric({
  icon,
  label,
  value,
  emphasis = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  emphasis?: boolean;
}) {
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className={emphasis ? "text-amber-600" : "text-campus"}>{icon}</div>
      <p className="mt-3 text-xs font-semibold text-slate-500">{label}</p>
      <p className="mt-1 truncate text-2xl font-bold text-ink">{value}</p>
    </article>
  );
}

function StatusBadge({ status }: { status: ModerationStatus }) {
  const styles: Record<ModerationStatus, string> = {
    APPROVED: "bg-emerald-50 text-emerald-700",
    HIDDEN: "bg-red-50 text-red-700",
    PENDING: "bg-amber-50 text-amber-700",
  };
  const labels: Record<ModerationStatus, string> = {
    APPROVED: "公開",
    HIDDEN: "已隱藏",
    PENDING: "待審核",
  };
  return (
    <span className={`rounded-md px-2 py-1 text-xs font-semibold ${styles[status]}`}>
      {labels[status]}
    </span>
  );
}

function AdminLoading() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-mist">
      <div className="flex items-center gap-2 text-sm text-slate-600">
        <Loader2 className="h-5 w-5 animate-spin text-campus" />
        正在確認管理員權限...
      </div>
    </main>
  );
}
