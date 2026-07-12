"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  CalendarDays,
  GraduationCap,
  Loader2,
  Search,
  UserRound,
} from "lucide-react";

import { useAppContext } from "@/components/AppContext";
import { getPublicApiBaseUrl } from "@/lib/public-runtime-config";

const API_BASE_URL = getPublicApiBaseUrl();
const DEBOUNCE_MS = 300;
const MIN_KEYWORD_LENGTH = 2;

export type SearchSuggestion = {
  resource_type: "course" | "instructor" | "event";
  resource_id: string;
  label: string;
  secondary_text: string | null;
  href: string;
};

type SearchAutoCompleteProps = {
  value: string;
  onChange: (value: string) => void;
  departmentId?: string | null;
  placeholder?: string;
  ariaLabel?: string;
  onSubmit?: (value: string) => void;
  onSelectSuggestion?: (suggestion: SearchSuggestion) => void;
  className?: string;
};

const suggestionIcons = {
  course: GraduationCap,
  instructor: UserRound,
  event: CalendarDays,
} satisfies Record<
  SearchSuggestion["resource_type"],
  typeof GraduationCap
>;

export function SearchAutoComplete({
  value,
  onChange,
  departmentId,
  placeholder,
  ariaLabel,
  onSubmit,
  onSelectSuggestion,
  className = "",
}: SearchAutoCompleteProps) {
  const router = useRouter();
  const { t } = useAppContext();
  const listboxId = useId();
  const skipNextRequestValue = useRef<string | null>(null);
  const [suggestions, setSuggestions] = useState<SearchSuggestion[]>([]);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [hasFocus, setHasFocus] = useState(false);
  const [hasQueried, setHasQueried] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  const normalizedKeyword = value.trim();
  const canQuery = normalizedKeyword.length >= MIN_KEYWORD_LENGTH;
  const showDropdown =
    hasFocus &&
    canQuery &&
    (isLoading || hasQueried || suggestions.length > 0);

  useEffect(() => {
    setActiveIndex(-1);
    if (skipNextRequestValue.current === normalizedKeyword) {
      skipNextRequestValue.current = null;
      return;
    }
    if (!canQuery) {
      setSuggestions([]);
      setHasQueried(false);
      setIsLoading(false);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setIsLoading(true);
      try {
        const params = new URLSearchParams({
          keyword: normalizedKeyword,
        });
        if (departmentId) {
          params.set("department_id", departmentId);
        }

        const response = await fetch(
          `${API_BASE_URL}/api/search/suggestions?${params.toString()}`,
          { signal: controller.signal },
        );
        if (!response.ok) {
          throw new Error(`Suggestion API failed with ${response.status}`);
        }
        setSuggestions((await response.json()) as SearchSuggestion[]);
        setHasQueried(true);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setSuggestions([]);
        setHasQueried(true);
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      }
    }, DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [canQuery, departmentId, normalizedKeyword]);

  function selectSuggestion(suggestion: SearchSuggestion) {
    skipNextRequestValue.current = suggestion.label.trim();
    onChange(suggestion.label);
    setSuggestions([]);
    setHasQueried(false);
    setHasFocus(false);
    if (onSelectSuggestion) {
      onSelectSuggestion(suggestion);
      return;
    }
    router.push(suggestion.href);
  }

  function submitCurrentValue() {
    if (!normalizedKeyword) {
      return;
    }
    setHasFocus(false);
    onSubmit?.(normalizedKeyword);
  }

  return (
    <div
      className={`relative ${className}`}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          setHasFocus(false);
          setActiveIndex(-1);
        }
      }}
    >
      <Search
        className="pointer-events-none absolute left-3 top-[22px] z-10 h-4 w-4 -translate-y-1/2 text-slate-400"
        aria-hidden="true"
      />
      <input
        type="search"
        role="combobox"
        aria-label={ariaLabel ?? t("search.default.aria")}
        aria-autocomplete="list"
        aria-expanded={showDropdown}
        aria-controls={showDropdown ? listboxId : undefined}
        aria-activedescendant={
          activeIndex >= 0
            ? `${listboxId}-option-${activeIndex}`
            : undefined
        }
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          setHasFocus(true);
        }}
        onFocus={() => setHasFocus(true)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && suggestions.length > 0) {
            event.preventDefault();
            setActiveIndex((current) =>
              current >= suggestions.length - 1 ? 0 : current + 1,
            );
            return;
          }
          if (event.key === "ArrowUp" && suggestions.length > 0) {
            event.preventDefault();
            setActiveIndex((current) =>
              current <= 0 ? suggestions.length - 1 : current - 1,
            );
            return;
          }
          if (event.key === "Enter") {
            event.preventDefault();
            if (activeIndex >= 0 && suggestions[activeIndex]) {
              selectSuggestion(suggestions[activeIndex]);
            } else {
              submitCurrentValue();
            }
            return;
          }
          if (event.key === "Escape") {
            setHasFocus(false);
            setActiveIndex(-1);
          }
        }}
        placeholder={placeholder ?? t("search.default.placeholder")}
        autoComplete="off"
        className="h-11 w-full rounded-md border border-slate-300 bg-white pl-10 pr-10 text-sm text-ink outline-none transition focus:border-campus focus:ring-4 focus:ring-campus/15 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500"
      />
      {isLoading && (
        <Loader2
          className="pointer-events-none absolute right-3 top-[22px] h-4 w-4 -translate-y-1/2 animate-spin text-campus"
          aria-label={t("search.loading")}
        />
      )}

      {showDropdown && (
        <div className="absolute inset-x-0 top-[50px] z-50 overflow-hidden rounded-lg border border-white/80 bg-white/90 shadow-xl backdrop-blur-md dark:border-slate-700/80 dark:bg-slate-900/90">
          {suggestions.length > 0 ? (
            <ul
              id={listboxId}
              role="listbox"
              aria-label={t("search.suggestions")}
              className="max-h-80 overflow-y-auto py-1.5"
            >
              {suggestions.map((suggestion, index) => {
                const Icon = suggestionIcons[suggestion.resource_type];
                const isActive = activeIndex === index;
                return (
                  <li
                    key={`${suggestion.resource_type}-${suggestion.resource_id}-${suggestion.label}`}
                    role="none"
                  >
                    <button
                      id={`${listboxId}-option-${index}`}
                      role="option"
                      aria-selected={isActive}
                      type="button"
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => selectSuggestion(suggestion)}
                      className={`flex min-h-14 w-full items-center gap-3 px-3 py-2 text-left transition ${
                        isActive
                          ? "bg-campus/10 dark:bg-teal-400/10"
                          : "hover:bg-slate-50 dark:hover:bg-slate-800"
                      }`}
                    >
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-slate-100 text-campus dark:bg-slate-800 dark:text-teal-300">
                        <Icon className="h-4 w-4" aria-hidden="true" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2">
                          <span className="truncate text-sm font-semibold text-ink dark:text-slate-100">
                            {suggestion.label}
                          </span>
                          <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                            {t(getSuggestionLabelKey(suggestion.resource_type))}
                          </span>
                        </span>
                        {suggestion.secondary_text && (
                          <span className="mt-1 block truncate text-xs text-slate-500 dark:text-slate-400">
                            {suggestion.secondary_text}
                          </span>
                        )}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            !isLoading && (
              <p className="px-4 py-5 text-center text-sm text-slate-500 dark:text-slate-400">
                {t("search.noResults")}
              </p>
            )
          )}
        </div>
      )}
    </div>
  );
}

function getSuggestionLabelKey(resourceType: SearchSuggestion["resource_type"]) {
  return {
    course: "search.type.course",
    instructor: "search.type.instructor",
    event: "search.type.event",
  }[resourceType] as
    | "search.type.course"
    | "search.type.instructor"
    | "search.type.event";
}
