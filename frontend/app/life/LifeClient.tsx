"use client";

import {
  FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  Beef,
  CheckCircle2,
  Home,
  Loader2,
  MapPin,
  MessageSquarePlus,
  Search,
  ShieldAlert,
  Star,
  Utensils,
  X,
  Flag,
} from "lucide-react";

import { USER_ROLES, useAppContext } from "@/components/AppContext";
import { FlagReviewModal } from "@/components/FlagReviewModal";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { getPublicApiBaseUrl } from "@/lib/public-runtime-config";
import type {
  LifeReview,
  LifeReviewType,
} from "@/lib/api-types";


const API_BASE_URL = getPublicApiBaseUrl();

type ReviewType = LifeReviewType;
type ReviewFilter = "all" | LifeReviewType;

type ReviewFormState = {
  reviewType: ReviewType;
  area: string;
  title: string;
  content: string;
  rating: number;
};

const filters: {
  value: ReviewFilter;
  icon: typeof Home;
}[] = [
  { value: "all", icon: Search },
  { value: "rental_warning", icon: AlertTriangle },
  { value: "food_recommendation", icon: Utensils },
  { value: "protein_meal_prep", icon: Beef },
];

const initialForm: ReviewFormState = {
  reviewType: "rental_warning",
  area: "",
  title: "",
  content: "",
  rating: 3,
};

export function LifeClient({
  initialReviews,
}: {
  initialReviews: LifeReview[];
}) {
  const { userRole, user, supabase, isAuthLoading, t } = useAppContext();
  const [activeFilter, setActiveFilter] = useState<ReviewFilter>("all");
  const [reviews, setReviews] = useState<LifeReview[]>(initialReviews);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [pendingReviewIds, setPendingReviewIds] = useState<Set<string>>(
    () => new Set(),
  );
  const isInitialFilterLoad = useRef(true);
  const skipNextFilterFetch = useRef(false);

  useEffect(() => {
    if (isInitialFilterLoad.current) {
      isInitialFilterLoad.current = false;
      return;
    }
    if (skipNextFilterFetch.current) {
      skipNextFilterFetch.current = false;
      return;
    }

    const controller = new AbortController();

    async function fetchReviews() {
      setIsLoading(true);
      setErrorMessage(null);

      try {
        const params = new URLSearchParams({ limit: "50" });
        if (activeFilter !== "all") {
          params.set("review_type", activeFilter);
        }
        const response = await fetch(
          `/api/life/reviews?${params.toString()}`,
          { signal: controller.signal },
        );
        if (!response.ok) {
          throw new Error(`Life reviews API failed with ${response.status}`);
        }
        setReviews((await response.json()) as LifeReview[]);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setErrorMessage(t("life.loadError"));
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      }
    }

    void fetchReviews();
    return () => controller.abort();
  }, [activeFilter, t]);

  const averageRating = useMemo(() => {
    const ratings = reviews.flatMap((review) =>
      review.rating === null ? [] : [review.rating],
    );
    if (ratings.length === 0) {
      return null;
    }
    return ratings.reduce((sum, rating) => sum + rating, 0) / ratings.length;
  }, [reviews]);

  return (
    <main className="min-h-screen bg-mist pb-16 dark:bg-[#081411]">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-sm font-semibold text-campus">{t("life.eyebrow")}</p>
              <h1 className="mt-1 text-2xl font-bold tracking-normal text-ink dark:text-slate-100 sm:text-3xl">
                {t("life.title")}
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600 dark:text-slate-300">
                {t("life.description")}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setIsModalOpen(true)}
                aria-haspopup="dialog"
                aria-expanded={isModalOpen}
                className="inline-flex h-11 items-center justify-center gap-2 rounded-md bg-campus px-4 text-sm font-semibold text-white shadow-sm transition hover:bg-campus/90"
              >
                <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
                {t("life.share")}
              </button>
              <ThemeSwitcher />
            </div>
          </div>

          <div className="mt-5 flex gap-2 overflow-x-auto pb-1" aria-label={t("life.filters.aria")}>
            {filters.map((filter) => {
              const Icon = filter.icon;
              const isActive = filter.value === activeFilter;
              return (
                <button
                  key={filter.value}
                  type="button"
                  onClick={() => setActiveFilter(filter.value)}
                  aria-pressed={isActive}
                  className={`flex h-10 shrink-0 items-center gap-2 rounded-md px-3 text-sm font-semibold transition ${
                    isActive
                      ? "bg-campus text-white"
                      : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                  }`}
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {getFilterLabel(filter.value, t)}
                </button>
              );
            })}
          </div>
        </div>
      </header>

      <section className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <div className="mb-5 flex items-center justify-between gap-3">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {isLoading ? t("life.loading") : t("life.count", { count: reviews.length })}
          </p>
          {averageRating !== null && (
            <p className="flex items-center gap-1 text-sm font-semibold text-slate-700 dark:text-slate-200">
              <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
              {t("life.average", { rating: averageRating.toFixed(1) })}
            </p>
          )}
        </div>

        {isLoading && <LoadingState />}
        {!isLoading && errorMessage && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-700 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-300">
            {errorMessage}
          </div>
        )}
        {!isLoading && !errorMessage && reviews.length === 0 && (
          <div className="rounded-lg border border-slate-200 bg-white p-8 text-center dark:border-slate-800 dark:bg-slate-900">
            <MessageSquarePlus className="mx-auto h-8 w-8 text-slate-400" />
            <p className="mt-3 text-sm text-slate-600 dark:text-slate-300">
              {t("life.empty")}
            </p>
          </div>
        )}
        {!isLoading && !errorMessage && reviews.length > 0 && (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {reviews.map((review) => (
              <ReviewCard
                key={review.id}
                review={review}
                isPendingReview={pendingReviewIds.has(review.id)}
                getAccessToken={async () => {
                  const { data } = await supabase?.auth.getSession() ?? {
                    data: { session: null },
                  };
                  return data.session?.access_token ?? null;
                }}
                onFlagged={() => {
                  setPendingReviewIds((current) => {
                    const next = new Set(current);
                    next.add(review.id);
                    return next;
                  });
                }}
              />
            ))}
          </div>
        )}
      </section>

      {isModalOpen && (
        <ReviewModal
          canSubmit={
            userRole === USER_ROLES.NCKU_VERIFIED ||
            userRole === USER_ROLES.ADMIN
          }
          isAuthLoading={isAuthLoading}
          email={user?.email ?? null}
          getAccessToken={async () => {
            const { data } = await supabase?.auth.getSession() ?? {
              data: { session: null },
            };
            return data.session?.access_token ?? null;
          }}
          onClose={() => setIsModalOpen(false)}
          onCreated={(createdReview) => {
            setIsModalOpen(false);
            setReviews((current) => [
              createdReview,
              ...current.filter(
                (review) => review.id !== createdReview.id,
              ),
            ]);
            if (activeFilter !== "all") {
              skipNextFilterFetch.current = true;
              setActiveFilter("all");
            }
          }}
        />
      )}
    </main>
  );
}

function ReviewCard({
  review,
  isPendingReview,
  getAccessToken,
  onFlagged,
}: {
  review: LifeReview;
  isPendingReview: boolean;
  getAccessToken: () => Promise<string | null>;
  onFlagged: () => void;
}) {
  const { t, locale } = useAppContext();
  const [isFlagModalOpen, setIsFlagModalOpen] = useState(false);

  if (isPendingReview) {
    return (
      <article
        id={`review-${review.id}`}
        tabIndex={0}
        className="flex min-h-64 scroll-mt-24 flex-col justify-center rounded-lg border border-amber-200 bg-amber-50/80 p-5 text-center shadow-sm dark:border-amber-800/70 dark:bg-amber-950/30"
      >
        <ShieldAlert className="mx-auto h-8 w-8 text-amber-700 dark:text-amber-300" aria-hidden="true" />
        <h2 className="mt-3 text-base font-bold text-amber-950 dark:text-amber-100">
          {t("flag.pending.title")}
        </h2>
        <p className="mt-2 text-sm leading-6 text-amber-900 dark:text-amber-200">
          {t("flag.pending.description")}
        </p>
      </article>
    );
  }

  return (
    <>
      <article
        id={`review-${review.id}`}
        tabIndex={0}
        className="flex min-h-64 scroll-mt-24 flex-col rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900"
      >
        <div className="flex items-start justify-between gap-3">
          <span className="rounded-md bg-campus/10 px-2 py-1 text-xs font-semibold text-campus dark:text-teal-300">
            {getReviewTypeLabel(review.review_type, t)}
          </span>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400 dark:text-slate-500">
              {new Intl.DateTimeFormat(locale, {
                timeZone: "Asia/Taipei",
                year: "numeric",
                month: "short",
                day: "numeric",
              }).format(new Date(review.created_at))}
            </span>
            <button
              type="button"
              onClick={() => setIsFlagModalOpen(true)}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-red-100 bg-red-50 text-red-600 transition hover:bg-red-100 focus:outline-none focus:ring-4 focus:ring-red-200 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-300 dark:hover:bg-red-900/50"
              aria-label={t("flag.button.aria", { title: review.title })}
              title={t("flag.button")}
            >
              <Flag className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        <h2 className="mt-3 text-lg font-bold tracking-normal text-ink dark:text-slate-100">
          {review.title}
        </h2>

      {(review.area || review.location_name) && (
        <p className="mt-2 flex items-start gap-1.5 text-sm text-slate-500 dark:text-slate-400">
          <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-brick" />
          {[review.area, review.location_name].filter(Boolean).join(" · ")}
        </p>
      )}

      {review.rating !== null && (
        <div className="mt-3 flex gap-0.5" aria-label={t("life.review.ratingAria", { rating: review.rating })}>
          {Array.from({ length: 5 }, (_, index) => (
            <Star
              key={index}
              className={`h-4 w-4 ${
                index < review.rating!
                  ? "fill-amber-400 text-amber-400"
                  : "text-slate-200"
              }`}
            />
          ))}
        </div>
      )}

      <p className="mt-3 line-clamp-5 text-sm leading-6 text-slate-600 dark:text-slate-300">
        {review.content}
      </p>

        <div className="mt-auto flex items-center justify-between gap-3 pt-4">
          <div className="flex flex-wrap gap-1.5">
            {review.tags.slice(0, 3).map((tag) => (
              <span
                key={tag}
                className="rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400"
              >
                {tag}
              </span>
            ))}
          </div>
          <span className="shrink-0 text-xs text-slate-400">
            @{review.author_alias ?? t("life.review.anonymous")}
          </span>
        </div>
      </article>

      <FlagReviewModal
        reviewId={review.id}
        reviewTitle={review.title}
        open={isFlagModalOpen}
        getAccessToken={getAccessToken}
        onClose={() => setIsFlagModalOpen(false)}
        onFlagged={onFlagged}
      />
    </>
  );
}

function ReviewModal({
  canSubmit,
  isAuthLoading,
  email,
  getAccessToken,
  onClose,
  onCreated,
}: {
  canSubmit: boolean;
  isAuthLoading: boolean;
  email: string | null;
  getAccessToken: () => Promise<string | null>;
  onClose: () => void;
  onCreated: (review: LifeReview) => void;
}) {
  const { t } = useAppContext();
  const [form, setForm] = useState<ReviewFormState>(initialForm);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const fieldsDisabled = !canSubmit || isAuthLoading || isSubmitting;

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit || !form.title.trim() || !form.content.trim()) {
      return;
    }

    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const accessToken = await getAccessToken();
      if (!accessToken) {
        setSubmitError(t("life.form.expired"));
        return;
      }

      const response = await fetch(`${API_BASE_URL}/api/life/reviews`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        body: JSON.stringify({
          review_type: form.reviewType,
          area: form.area.trim() || null,
          title: form.title.trim(),
          content: form.content.trim(),
          rating: form.rating,
          author_alias: email?.split("@", 1)[0] ?? null,
          tags: getReviewTags(form.reviewType),
          metadata: { source: "life_page_modal" },
        }),
      });

      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        setSubmitError(payload?.detail ?? t("life.form.failed"));
        return;
      }
      onCreated((await response.json()) as LifeReview);
    } catch {
      setSubmitError(t("life.form.network"));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 p-0 sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="review-modal-title"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <div className="max-h-[92vh] w-full overflow-y-auto rounded-t-lg bg-white shadow-xl dark:bg-slate-900 sm:max-w-xl sm:rounded-lg">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
          <div>
            <p className="text-xs font-semibold text-campus">{t("life.modal.eyebrow")}</p>
            <h2
              id="review-modal-title"
              className="text-lg font-bold tracking-normal text-ink dark:text-slate-100"
            >
              {t("life.modal.title")}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
            aria-label={t("life.modal.close")}
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="p-4">
          {!canSubmit && !isAuthLoading && (
            <div className="mb-4 flex items-start gap-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm leading-6 text-amber-900 dark:border-amber-700/70 dark:bg-amber-950/40 dark:text-amber-200">
              <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0" />
              <p>
                {t("life.modal.warning")}
              </p>
            </div>
          )}

          {canSubmit && (
            <div className="mb-4 flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
              <CheckCircle2 className="h-4 w-4" />
              {t("life.modal.verified")}
            </div>
          )}

          <form className="grid gap-4" onSubmit={handleSubmit}>
            <div className="grid gap-4 sm:grid-cols-2">
              <FieldLabel label={t("life.form.type")}>
                <select
                  value={form.reviewType}
                  disabled={fieldsDisabled}
                  onChange={(event) =>
                    setForm((previous) => ({
                      ...previous,
                      reviewType: event.target.value as ReviewType,
                    }))
                  }
                  className={inputClassName}
                >
                  <option value="rental_warning">{t("life.review.rentalWarning")}</option>
                  <option value="rental_recommendation">{t("life.review.rentalRecommendation")}</option>
                  <option value="food_recommendation">{t("life.review.food")}</option>
                  <option value="protein_meal_prep">{t("life.review.protein")}</option>
                  <option value="other">{t("life.review.other")}</option>
                </select>
              </FieldLabel>

              <FieldLabel label={t("life.form.area")}>
                <input
                  value={form.area}
                  disabled={fieldsDisabled}
                  onChange={(event) =>
                    setForm((previous) => ({
                      ...previous,
                      area: event.target.value,
                    }))
                  }
                  maxLength={120}
                  placeholder={t("life.form.area.placeholder")}
                  className={inputClassName}
                />
              </FieldLabel>
            </div>

            <FieldLabel label={t("life.form.title")}>
              <input
                value={form.title}
                disabled={fieldsDisabled}
                onChange={(event) =>
                  setForm((previous) => ({
                    ...previous,
                    title: event.target.value,
                  }))
                }
                minLength={2}
                maxLength={160}
                required
                placeholder={t("life.form.title.placeholder")}
                className={inputClassName}
              />
            </FieldLabel>

            <FieldLabel label={t("life.form.content")}>
              <textarea
                value={form.content}
                disabled={fieldsDisabled}
                onChange={(event) =>
                  setForm((previous) => ({
                    ...previous,
                    content: event.target.value,
                  }))
                }
                minLength={5}
                maxLength={4000}
                required
                rows={5}
                placeholder={t("life.form.content.placeholder")}
                className={`${inputClassName} h-auto resize-none py-2`}
              />
            </FieldLabel>

            <fieldset disabled={fieldsDisabled}>
              <legend className="text-sm font-medium text-slate-700 dark:text-slate-300">
                {t("life.form.rating")}
              </legend>
              <div className="mt-2 flex gap-2">
                {Array.from({ length: 5 }, (_, index) => {
                  const rating = index + 1;
                  return (
                    <button
                      key={rating}
                      type="button"
                      onClick={() =>
                        setForm((previous) => ({ ...previous, rating }))
                      }
                      className="flex h-10 w-10 items-center justify-center rounded-md border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-950 disabled:bg-slate-100 dark:disabled:bg-slate-800"
                      aria-label={t("life.form.ratingValue", { rating })}
                    >
                      <Star
                        className={`h-5 w-5 ${
                          rating <= form.rating
                            ? "fill-amber-400 text-amber-400"
                            : "text-slate-300"
                        }`}
                      />
                    </button>
                  );
                })}
              </div>
            </fieldset>

            {submitError && (
              <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">
                {submitError}
              </p>
            )}

            <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold leading-5 text-amber-900 dark:border-amber-800/70 dark:bg-amber-950/40 dark:text-amber-200">
              {t("life.form.legalNotice")}
            </p>

            <div className="flex flex-col-reverse gap-2 border-t border-slate-200 pt-4 dark:border-slate-800 sm:flex-row sm:justify-end">
              <button
                type="button"
                onClick={onClose}
                className="h-10 rounded-md border border-slate-200 px-4 text-sm font-semibold text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
              >
                {t("life.form.cancel")}
              </button>
              <button
                type="submit"
                disabled={
                  fieldsDisabled ||
                  !form.title.trim() ||
                  !form.content.trim()
                }
                className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-campus px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
                {t("life.form.submit")}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}

const inputClassName =
  "mt-2 h-11 w-full rounded-md border border-slate-300 bg-white px-3 text-sm text-ink outline-none focus:border-campus focus:ring-4 focus:ring-campus/15 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100 dark:placeholder:text-slate-500 dark:disabled:bg-slate-800 dark:disabled:text-slate-500";

function FieldLabel({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="text-sm font-medium text-slate-700 dark:text-slate-300">
      {label}
      {children}
    </label>
  );
}

function LoadingState() {
  const { t } = useAppContext();
  return (
    <div className="flex h-56 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
      <Loader2 className="mr-2 h-5 w-5 animate-spin text-campus" />
      {t("life.loading")}
    </div>
  );
}

function getFilterLabel(
  value: ReviewFilter,
  t: ReturnType<typeof useAppContext>["t"],
) {
  const labels = {
    all: "life.filter.all",
    rental_warning: "life.filter.rentalWarning",
    rental_recommendation: "life.review.rentalRecommendation",
    food_recommendation: "life.filter.food",
    protein_meal_prep: "life.filter.protein",
    other: "life.review.other",
  } as const;

  return t(labels[value]);
}

function getReviewTypeLabel(
  value: ReviewType,
  t: ReturnType<typeof useAppContext>["t"],
) {
  const labels = {
    rental_warning: "life.review.rentalWarning",
    rental_recommendation: "life.review.rentalRecommendation",
    food_recommendation: "life.review.food",
    protein_meal_prep: "life.review.protein",
    other: "life.review.other",
  } as const;

  return t(labels[value]);
}

function getReviewTags(reviewType: ReviewType): string[] {
  const tags: Record<ReviewType, string[]> = {
    rental_warning: ["租屋", "避雷"],
    rental_recommendation: ["租屋", "推薦"],
    food_recommendation: ["美食", "推薦"],
    protein_meal_prep: ["高蛋白", "備餐"],
    other: ["生活"],
  };
  return tags[reviewType];
}
