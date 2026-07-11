"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CalendarDays,
  ExternalLink,
  Loader2,
  MapPin,
  ShieldCheck,
  Ticket,
} from "lucide-react";

import { ThemeSwitcher } from "@/components/ThemeSwitcher";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

type ActivityType =
  | "club"
  | "official_event"
  | "party"
  | "bike_festival"
  | "lecture"
  | "competition"
  | "other";

type Activity = {
  id: string;
  activity_type: ActivityType;
  title: string;
  organizer_name: string | null;
  description: string | null;
  location: string | null;
  start_at: string | null;
  end_at: string | null;
  registration_url: string | null;
  official_url: string | null;
  cover_image_url: string | null;
  tags: string[];
  is_official: boolean;
};

const typeLabels: Record<ActivityType, string> = {
  club: "社團活動",
  official_event: "官方活動",
  party: "舞會",
  bike_festival: "單車節",
  lecture: "校園講座",
  competition: "競賽",
  other: "其他活動",
};

export default function EventsPage() {
  const [events, setEvents] = useState<Activity[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function fetchEvents() {
      setIsLoading(true);
      setErrorMessage(null);

      try {
        const params = new URLSearchParams({
          upcoming_only: "true",
          limit: "50",
        });
        const response = await fetch(
          `${API_BASE_URL}/api/events?${params.toString()}`,
          { signal: controller.signal },
        );
        if (!response.ok) {
          throw new Error(`Events API failed with ${response.status}`);
        }
        setEvents((await response.json()) as Activity[]);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setErrorMessage("目前無法取得活動資料，請稍後再試。");
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      }
    }

    void fetchEvents();
    return () => controller.abort();
  }, []);

  const monthCount = useMemo(
    () =>
      new Set(
        events
          .filter((event) => event.start_at)
          .map((event) => event.start_at?.slice(0, 7)),
      ).size,
    [events],
  );

  return (
    <main className="min-h-screen bg-mist pb-16 dark:bg-[#081411]">
      <header className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-sm font-semibold text-campus">活動板塊</p>
              <h1 className="mt-1 text-2xl font-bold tracking-normal text-ink dark:text-slate-100 sm:text-3xl">
                校園近期活動
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600 dark:text-slate-300">
                按時間整理官方活動、社團聚會、舞會與單車節，活動細節以主辦單位公告為準。
              </p>
            </div>
            <div className="flex items-center gap-3">
              <Stat value={events.length} label="近期活動" />
              <Stat value={monthCount} label="涵蓋月份" />
              <ThemeSwitcher />
            </div>
          </div>
        </div>
      </header>

      <section className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        {isLoading && <LoadingState />}

        {!isLoading && errorMessage && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-700 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-300">
            {errorMessage}
          </div>
        )}

        {!isLoading && !errorMessage && events.length === 0 && (
          <div className="rounded-lg border border-slate-200 bg-white p-8 text-center dark:border-slate-800 dark:bg-slate-900">
            <CalendarDays className="mx-auto h-8 w-8 text-slate-400" />
            <p className="mt-3 text-sm text-slate-600 dark:text-slate-300">目前沒有即將舉行的活動。</p>
          </div>
        )}

        {!isLoading && !errorMessage && events.length > 0 && (
          <div className="relative grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {events.map((event) => (
              <EventCard key={event.id} event={event} />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

function EventCard({ event }: { event: Activity }) {
  const start = event.start_at ? new Date(event.start_at) : null;
  const hasActionUrl = Boolean(event.official_url ?? event.registration_url);
  const actionUrl = `${API_BASE_URL}/api/events/${event.id}/visit`;

  return (
    <article
      id={`event-${event.id}`}
      className="flex min-h-[360px] scroll-mt-24 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-md dark:border-slate-800 dark:bg-slate-900"
    >
      <div className="relative h-36 overflow-hidden bg-campus">
        {event.cover_image_url ? (
          // External event images are data-driven and may come from different official hosts.
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={event.cover_image_url}
            alt=""
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full items-center justify-center bg-campus text-white">
            <CalendarDays className="h-12 w-12 opacity-80" aria-hidden="true" />
          </div>
        )}
        <span className="absolute left-3 top-3 rounded-md bg-white px-2 py-1 text-xs font-semibold text-campus shadow-sm">
          {typeLabels[event.activity_type]}
        </span>
        {event.is_official && (
          <span className="absolute right-3 top-3 flex items-center gap-1 rounded-md bg-emerald-600 px-2 py-1 text-xs font-semibold text-white">
            <ShieldCheck className="h-3.5 w-3.5" />
            官方
          </span>
        )}
      </div>

      <div className="flex flex-1 flex-col p-4">
        <div className="flex gap-3">
          <div className="flex h-14 w-14 shrink-0 flex-col items-center justify-center rounded-md bg-mist text-campus dark:bg-slate-800 dark:text-teal-300">
            <span className="text-xs font-semibold">
              {start ? `${start.getMonth() + 1}月` : "日期"}
            </span>
            <span className="text-xl font-bold">
              {start ? start.getDate() : "待定"}
            </span>
          </div>
          <div className="min-w-0">
            <h2 className="text-lg font-bold tracking-normal text-ink dark:text-slate-100">
              {event.title}
            </h2>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              {event.organizer_name ?? "主辦單位待公告"}
            </p>
          </div>
        </div>

        <div className="mt-4 space-y-2 text-sm text-slate-600 dark:text-slate-300">
          <p className="flex items-start gap-2">
            <CalendarDays className="mt-0.5 h-4 w-4 shrink-0 text-campus" />
            {formatEventTime(event.start_at, event.end_at)}
          </p>
          <p className="flex items-start gap-2">
            <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-brick" />
            {event.location ?? "地點待公告"}
          </p>
        </div>

        {event.description && (
          <p className="mt-3 line-clamp-3 text-sm leading-6 text-slate-600 dark:text-slate-300">
            {event.description}
          </p>
        )}

        <div className="mt-auto pt-4">
          {hasActionUrl ? (
            <a
              href={actionUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-campus px-4 text-sm font-semibold text-white transition hover:bg-campus/90"
            >
              {event.registration_url ? (
                <Ticket className="h-4 w-4" />
              ) : (
                <ExternalLink className="h-4 w-4" />
              )}
              查看官方活動資訊
            </a>
          ) : (
            <span className="flex h-10 w-full items-center justify-center rounded-md bg-slate-100 text-sm text-slate-500 dark:bg-slate-800 dark:text-slate-400">
              官方連結待公告
            </span>
          )}
        </div>
      </div>
    </article>
  );
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="min-w-24 rounded-md border border-slate-200 bg-white px-3 py-2 text-center dark:border-slate-700 dark:bg-slate-900">
      <p className="text-xl font-bold text-ink dark:text-slate-100">{value}</p>
      <p className="text-xs text-slate-500 dark:text-slate-400">{label}</p>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex h-56 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
      <Loader2 className="mr-2 h-5 w-5 animate-spin text-campus" />
      正在整理近期活動...
    </div>
  );
}

function formatEventTime(startAt: string | null, endAt: string | null) {
  if (!startAt) {
    return "日期時間待公告";
  }

  const formatter = new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    month: "long",
    day: "numeric",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
  const start = formatter.format(new Date(startAt));
  if (!endAt) {
    return start;
  }
  const end = new Intl.DateTimeFormat("zh-TW", {
    timeZone: "Asia/Taipei",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(endAt));
  return `${start} - ${end}`;
}
