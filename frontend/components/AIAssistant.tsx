"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Bot, MessageSquareText, Send, X } from "lucide-react";

import { useAppContext } from "@/components/AppContext";
import { getPublicApiBaseUrl } from "@/lib/public-runtime-config";

const API_BASE_URL = getPublicApiBaseUrl();
const CHAT_API_URL = `${API_BASE_URL}/api/chat`;
const CHAT_SESSION_STORAGE_KEY = "ncku_ai_chat_session_id";

const MarkdownMessage = dynamic(
  () =>
    import("@/components/MarkdownMessage").then(
      (module) => module.MarkdownMessage,
    ),
  {
    ssr: false,
    loading: () => (
      <div className="space-y-2" aria-label="Loading Markdown response">
        <div className="h-4 w-11/12 animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
        <div className="h-4 w-3/4 animate-pulse rounded bg-slate-100 dark:bg-slate-800" />
      </div>
    ),
  },
);

type Citation = {
  source_title: string | null;
  source_url: string | null;
  source_type: string;
  category: string;
  department: string | null;
  chunk_index: number;
  similarity: number;
  excerpt: string;
};

type ChatApiResponse = {
  answer: string;
  citations: Citation[];
  retrieved_count: number;
};

function createSessionId() {
  if (typeof window === "undefined") {
    return "";
  }

  const existingSessionId = window.localStorage.getItem(CHAT_SESSION_STORAGE_KEY);
  if (existingSessionId) {
    return existingSessionId;
  }

  const nextSessionId = crypto.randomUUID();
  window.localStorage.setItem(CHAT_SESSION_STORAGE_KEY, nextSessionId);
  return nextSessionId;
}

type ChatMessage = {
  id: string;
  role: "assistant" | "user";
  content: string;
  citations?: Citation[];
};

type AIAssistantSidebarProps = {
  open: boolean;
  departmentFilter: string;
  autoPrompt?: string | null;
  onOpenChange: (open: boolean) => void;
  onAutoPromptConsumed?: () => void;
};

export function AIAssistantSidebar({
  open,
  departmentFilter,
  autoPrompt,
  onOpenChange,
  onAutoPromptConsumed,
}: AIAssistantSidebarProps) {
  const { t } = useAppContext();
  const assistantTitleId = "ai-assistant-title";
  const assistantPanelId = "ai-assistant-panel";
  const [messages, setMessages] = useState<ChatMessage[]>(() => [
    {
      id: "welcome",
      role: "assistant",
      content: t("ai.welcome"),
    },
  ]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId, setSessionId] = useState("");
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const lastAutoPromptRef = useRef<string | null>(null);

  useEffect(() => {
    setSessionId(createSessionId());
  }, []);

  useEffect(() => {
    setMessages((current) =>
      current.map((message) =>
        message.id === "welcome"
          ? { ...message, content: t("ai.welcome") }
          : message,
      ),
    );
  }, [t]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isLoading, open]);

  const sendMessage = useCallback(async (rawQuery: string) => {
    const userQuery = rawQuery.trim();
    if (!userQuery || isLoading) {
      return;
    }

    const activeSessionId = sessionId || createSessionId();
    if (!sessionId) {
      setSessionId(activeSessionId);
    }

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: userQuery,
    };

    setMessages((current) => [...current, userMessage]);
    setInput("");
    setIsLoading(true);

    try {
      const response = await fetch(CHAT_API_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          session_id: activeSessionId,
          user_query: userQuery,
          department_filter: departmentFilter,
        }),
      });

      if (!response.ok) {
        throw new Error(`Chat API failed with ${response.status}`);
      }

      const data = (await response.json()) as ChatApiResponse;
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: data.answer,
          citations: data.citations,
        },
      ]);
    } catch {
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: t("ai.error"),
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }, [departmentFilter, isLoading, sessionId, t]);

  useEffect(() => {
    if (!autoPrompt) {
      lastAutoPromptRef.current = null;
      return;
    }

    if (!open || lastAutoPromptRef.current === autoPrompt) {
      return;
    }

    lastAutoPromptRef.current = autoPrompt;
    setInput(autoPrompt);
    onAutoPromptConsumed?.();
  }, [autoPrompt, onAutoPromptConsumed, open]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await sendMessage(input);
  }

  return (
    <>
      <button
        type="button"
        onClick={() => onOpenChange(true)}
        className="fixed bottom-5 right-5 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-brick text-white shadow-soft transition hover:scale-105 focus:outline-none focus:ring-4 focus:ring-brick/25"
        aria-label={t("ai.fab.open")}
        aria-controls={assistantPanelId}
        aria-expanded={open}
      >
        <Bot className="h-6 w-6" aria-hidden="true" />
      </button>

      <div
        className={`fixed inset-0 z-50 transition ${open ? "pointer-events-auto" : "pointer-events-none"}`}
        aria-hidden={!open}
      >
        <button
          type="button"
          className={`absolute inset-0 bg-ink/35 transition-opacity ${open ? "opacity-100" : "opacity-0"}`}
          onClick={() => onOpenChange(false)}
          aria-label={t("ai.overlay.close")}
        />

        <aside
          id={assistantPanelId}
          className={`absolute right-0 top-0 flex h-full w-full max-w-md flex-col border-l border-white/60 bg-white/90 shadow-soft backdrop-blur-md transition-transform duration-300 dark:border-slate-700/80 dark:bg-slate-950/90 sm:w-[420px] ${
            open ? "translate-x-0" : "translate-x-full"
          }`}
          role="dialog"
          aria-modal="true"
          aria-labelledby={assistantTitleId}
        >
          <header className="flex h-16 items-center justify-between border-b border-white/70 bg-white/70 px-4 backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/70">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-brick text-white">
                <MessageSquareText className="h-5 w-5" aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <h2 id={assistantTitleId} className="truncate text-base font-bold text-ink dark:text-slate-100">{t("ai.title")}</h2>
                <p className="truncate text-xs text-slate-500 dark:text-slate-400">{t("ai.department", { department: departmentFilter })}</p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => onOpenChange(false)}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-slate-500 hover:bg-slate-100 hover:text-ink dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white"
              aria-label={t("ai.close")}
            >
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
          </header>

          <div className="flex-1 space-y-4 overflow-y-auto bg-mist px-4 py-5 dark:bg-[#081411]" aria-live="polite">
            {messages.map((message) => (
              <ChatBubble key={message.id} message={message} />
            ))}

            {isLoading && (
              <div className="max-w-[82%] rounded-lg bg-white p-3 text-sm leading-6 text-slate-700 shadow-sm dark:bg-slate-900 dark:text-slate-300">
                <div className="flex items-center gap-2">
                  <span className="h-2 w-2 rounded-full bg-campus animate-pulse" />
                  <span className="h-2 w-2 animate-pulse rounded-full bg-campus/70 [animation-delay:150ms]" />
                  <span className="h-2 w-2 animate-pulse rounded-full bg-campus/40 [animation-delay:300ms]" />
                  <span className="ml-1 text-slate-500 dark:text-slate-300">{t("ai.loading")}</span>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          <form className="border-t border-white/70 bg-white/75 p-4 backdrop-blur-md dark:border-slate-800 dark:bg-slate-950/75" onSubmit={handleSubmit}>
            <div className="flex items-center gap-2 rounded-lg border border-slate-300 bg-white p-2 focus-within:border-campus focus-within:ring-4 focus-within:ring-campus/15 dark:border-slate-700 dark:bg-slate-900">
              <input
                type="text"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder={t("ai.input.placeholder")}
                aria-label={t("ai.input.aria")}
                className="min-w-0 flex-1 border-0 bg-transparent px-2 text-sm text-ink outline-none placeholder:text-slate-400 dark:text-slate-100 dark:placeholder:text-slate-500"
                disabled={isLoading}
              />
              <button
                type="submit"
                disabled={isLoading || !input.trim()}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-campus text-white transition disabled:cursor-not-allowed disabled:bg-slate-300"
                aria-label={t("ai.submit")}
              >
                <Send className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </form>
        </aside>
      </div>
    </>
  );
}

function ChatBubble({ message }: { message: ChatMessage }) {
  const { t } = useAppContext();
  const isUser = message.role === "user";

  return (
    <div
      className={`max-w-[86%] rounded-lg p-3 text-sm leading-6 shadow-sm ${
        isUser ? "ml-auto bg-campus text-white" : "bg-white text-slate-700 dark:bg-slate-900 dark:text-slate-300"
      }`}
    >
      {isUser ? (
        <p className="whitespace-pre-wrap">{message.content}</p>
      ) : (
        <MarkdownMessage content={message.content} />
      )}

      {!isUser && message.citations && message.citations.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2 border-t border-slate-200 pt-3 dark:border-slate-700">
          {message.citations.map((citation, index) => (
            <CitationBadge
              key={`${citation.source_url ?? citation.source_type}-${citation.chunk_index}-${index}`}
              citation={citation}
              sourceLabel={t("ai.citation", { index: index + 1 })}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function CitationBadge({
  citation,
  sourceLabel,
}: {
  citation: Citation;
  sourceLabel: string;
}) {
  const label = citation.source_title || citation.department || citation.source_type;
  const similarity = Math.round(citation.similarity * 100);

  const content = (
    <>
      <span className="font-semibold">{sourceLabel}</span>
      <span className="max-w-[150px] truncate">{label}</span>
      <span className="text-slate-400">{similarity}%</span>
    </>
  );

  if (citation.source_url) {
    return (
      <a
        href={citation.source_url}
        target="_blank"
        rel="noreferrer"
        title={citation.excerpt}
        className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-campus/20 bg-campus/10 px-2 py-1 text-xs text-campus hover:bg-campus/15"
      >
        {content}
      </a>
    );
  }

  return (
    <span
      title={citation.excerpt}
      className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
    >
      {content}
    </span>
  );
}
