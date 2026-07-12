"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ArrowUpRight,
  Bell,
  CalendarDays,
  MessageSquareText,
  X,
} from "lucide-react";
import { useRouter } from "next/navigation";

import { useAppContext } from "@/components/AppContext";

const MAX_VISIBLE_NOTIFICATIONS = 3;
const AUTO_DISMISS_MS = 10_000;
const HEARTBEAT_MS = 25_000;
const MAX_RECONNECT_DELAY_MS = 30_000;

type NotificationKind = "review.approved" | "event.created";

export type RealtimeNotification = {
  id: string;
  kind: NotificationKind;
  topic: string;
  title: string;
  summary: string;
  href: string;
  resource_id: string | null;
  created_at: string;
};

type NotificationEnvelope = {
  event: "notification";
  data: RealtimeNotification;
};

function isNotificationEnvelope(value: unknown): value is NotificationEnvelope {
  if (!value || typeof value !== "object") {
    return false;
  }

  const envelope = value as Record<string, unknown>;
  if (envelope.event !== "notification" || !envelope.data) {
    return false;
  }

  const data = envelope.data as Record<string, unknown>;
  return (
    typeof data.id === "string" &&
    (data.kind === "review.approved" || data.kind === "event.created") &&
    typeof data.title === "string" &&
    typeof data.summary === "string" &&
    typeof data.href === "string" &&
    data.href.startsWith("/") &&
    !data.href.startsWith("//")
  );
}

function getWebSocketUrl(departmentId: string): string {
  const configuredWebSocketUrl = process.env.NEXT_PUBLIC_WS_URL;
  const configuredApiUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
  const source =
    configuredApiUrl ??
    configuredWebSocketUrl ??
    window.location.origin;
  const url = new URL(source, window.location.origin);

  if (configuredApiUrl || !configuredWebSocketUrl) {
    url.pathname = "/ws/notifications";
    url.search = "";
  }

  if (url.protocol === "http:") {
    url.protocol = "ws:";
  } else if (url.protocol === "https:") {
    url.protocol = "wss:";
  }

  if (departmentId) {
    url.searchParams.set("department_id", departmentId);
  }
  return url.toString();
}

export function useRealtimeNotifications(departmentId: string) {
  const [notifications, setNotifications] = useState<
    RealtimeNotification[]
  >([]);
  const autoDismissTimers = useRef(new Map<string, number>());

  const dismiss = useCallback((notificationId: string) => {
    setNotifications((current) =>
      current.filter((notification) => notification.id !== notificationId),
    );
    const timer = autoDismissTimers.current.get(notificationId);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      autoDismissTimers.current.delete(notificationId);
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    let reconnectAttempt = 0;
    let reconnectTimer: number | null = null;
    let heartbeatTimer: number | null = null;
    let socket: WebSocket | null = null;

    const clearConnectionTimers = () => {
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (heartbeatTimer !== null) {
        window.clearInterval(heartbeatTimer);
        heartbeatTimer = null;
      }
    };

    const addNotification = (notification: RealtimeNotification) => {
      setNotifications((current) => [
        notification,
        ...current.filter((item) => item.id !== notification.id),
      ].slice(0, MAX_VISIBLE_NOTIFICATIONS));

      const previousTimer = autoDismissTimers.current.get(notification.id);
      if (previousTimer !== undefined) {
        window.clearTimeout(previousTimer);
      }
      const timer = window.setTimeout(() => {
        setNotifications((current) =>
          current.filter((item) => item.id !== notification.id),
        );
        autoDismissTimers.current.delete(notification.id);
      }, AUTO_DISMISS_MS);
      autoDismissTimers.current.set(notification.id, timer);
    };

    let connect: () => void;

    const scheduleReconnect = () => {
      if (
        disposed ||
        !navigator.onLine ||
        reconnectTimer !== null
      ) {
        return;
      }

      const exponentialDelay = Math.min(
        1_000 * 2 ** reconnectAttempt,
        MAX_RECONNECT_DELAY_MS,
      );
      const jitter = Math.floor(Math.random() * 500);
      reconnectAttempt += 1;
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, exponentialDelay + jitter);
    };

    connect = () => {
      if (
        disposed ||
        !navigator.onLine ||
        socket?.readyState === WebSocket.OPEN ||
        socket?.readyState === WebSocket.CONNECTING
      ) {
        return;
      }

      try {
        const nextSocket = new WebSocket(getWebSocketUrl(departmentId));
        socket = nextSocket;

        nextSocket.onopen = () => {
          reconnectAttempt = 0;
          if (reconnectTimer !== null) {
            window.clearTimeout(reconnectTimer);
            reconnectTimer = null;
          }
          heartbeatTimer = window.setInterval(() => {
            if (nextSocket.readyState === WebSocket.OPEN) {
              nextSocket.send(JSON.stringify({ action: "ping" }));
            }
          }, HEARTBEAT_MS);
        };

        nextSocket.onmessage = (event) => {
          if (typeof event.data !== "string") {
            return;
          }
          try {
            const message: unknown = JSON.parse(event.data);
            if (isNotificationEnvelope(message)) {
              addNotification(message.data);
            }
          } catch {
            // Ignore malformed server frames and keep the healthy socket alive.
          }
        };

        nextSocket.onerror = () => {
          nextSocket.close();
        };

        nextSocket.onclose = () => {
          if (heartbeatTimer !== null) {
            window.clearInterval(heartbeatTimer);
            heartbeatTimer = null;
          }
          if (socket === nextSocket) {
            socket = null;
            scheduleReconnect();
          }
        };
      } catch {
        scheduleReconnect();
      }
    };

    const handleOnline = () => {
      reconnectAttempt = 0;
      connect();
    };
    const handleOffline = () => {
      socket?.close(1000, "browser_offline");
    };

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);
    connect();

    return () => {
      disposed = true;
      clearConnectionTimers();
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
      if (
        socket?.readyState === WebSocket.OPEN ||
        socket?.readyState === WebSocket.CONNECTING
      ) {
        socket.close(1000, "component_unmounted");
      }
      socket = null;
    };
  }, [departmentId]);

  useEffect(
    () => () => {
      autoDismissTimers.current.forEach((timer) => {
        window.clearTimeout(timer);
      });
      autoDismissTimers.current.clear();
    },
    [],
  );

  return { notifications, dismiss };
}

export function RealtimeNotifications() {
  const router = useRouter();
  const { currentDepartment } = useAppContext();
  const { notifications, dismiss } = useRealtimeNotifications(
    currentDepartment.id,
  );

  return (
    <aside
      aria-label="即時通知"
      aria-live="polite"
      className="pointer-events-none fixed right-3 top-20 z-[70] flex w-[calc(100vw-1.5rem)] max-w-sm flex-col gap-3 sm:right-5 sm:top-24"
    >
      {notifications.map((notification) => {
        const isEvent = notification.kind === "event.created";
        const Icon = isEvent ? CalendarDays : MessageSquareText;

        return (
          <section
            key={notification.id}
            data-testid="realtime-notification"
            className="pointer-events-auto animate-[notification-in_240ms_ease-out] overflow-hidden rounded-lg border border-white/70 bg-white/90 shadow-soft backdrop-blur-md dark:border-slate-700/80 dark:bg-slate-900/90"
          >
            <div className="flex items-start gap-3 p-4">
              <span className="relative grid h-10 w-10 shrink-0 place-items-center rounded-full bg-campus/10 text-campus dark:bg-emerald-400/10 dark:text-emerald-300">
                <Icon className="h-5 w-5" aria-hidden="true" />
                <span className="absolute right-0 top-0 h-2.5 w-2.5 animate-pulse rounded-full bg-brick ring-2 ring-white dark:ring-slate-900" />
              </span>

              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-1.5 text-xs font-semibold text-campus dark:text-emerald-300">
                  <Bell className="h-3.5 w-3.5" aria-hidden="true" />
                  即時校園通知
                </p>
                <h2 className="mt-1 text-sm font-bold tracking-normal text-ink dark:text-slate-100">
                  {notification.title}
                </h2>
                <p className="mt-1 line-clamp-3 text-sm leading-5 text-slate-600 dark:text-slate-300">
                  {notification.summary}
                </p>

                <button
                  type="button"
                  onClick={() => {
                    dismiss(notification.id);
                    router.push(notification.href);
                  }}
                  className="mt-3 inline-flex items-center gap-1.5 text-sm font-semibold text-campus transition hover:text-teal-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-campus focus-visible:ring-offset-2 dark:text-emerald-300 dark:hover:text-emerald-200 dark:focus-visible:ring-offset-slate-900"
                >
                  點擊前往
                  <ArrowUpRight className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>

              <button
                type="button"
                onClick={() => dismiss(notification.id)}
                aria-label="關閉通知"
                title="關閉通知"
                className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-campus dark:hover:bg-slate-800 dark:hover:text-slate-200"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </section>
        );
      })}
    </aside>
  );
}
