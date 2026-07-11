"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Languages } from "lucide-react";

import { useAppContext } from "@/components/AppContext";
import type { Locale } from "@/lib/i18n";

const localeOptions: { value: Locale; labelKey: "language.zhTW" | "language.enUS" }[] = [
  { value: "zh-TW", labelKey: "language.zhTW" },
  { value: "en-US", labelKey: "language.enUS" },
];

export function LanguageSwitcher() {
  const { locale, setLocale, t } = useAppContext();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setIsOpen(false);
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        className="flex h-10 min-w-10 items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-4 focus:ring-campus/20 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800"
        onClick={() => setIsOpen((open) => !open)}
        aria-label={t("language.switcher")}
        aria-haspopup="menu"
        aria-expanded={isOpen}
      >
        <Languages className="h-4 w-4" aria-hidden="true" />
        <span className="hidden sm:inline">{locale === "zh-TW" ? "中文" : "EN"}</span>
      </button>

      {isOpen && (
        <div
          className="absolute right-0 top-12 z-50 w-44 overflow-hidden rounded-lg border border-white/70 bg-white/95 p-1.5 shadow-xl backdrop-blur-md dark:border-slate-700/80 dark:bg-slate-900/95"
          role="menu"
          aria-label={t("language.menu")}
        >
          {localeOptions.map((option) => {
            const isSelected = locale === option.value;

            return (
              <button
                key={option.value}
                type="button"
                className={`flex h-10 w-full items-center gap-2 rounded-md px-2.5 text-sm transition focus:outline-none focus:ring-2 focus:ring-campus/40 ${
                  isSelected
                    ? "bg-campus text-white"
                    : "text-slate-700 hover:bg-slate-100 dark:text-slate-100 dark:hover:bg-slate-800"
                }`}
                onClick={() => {
                  setLocale(option.value);
                  setIsOpen(false);
                }}
                role="menuitemradio"
                aria-checked={isSelected}
              >
                <span className="flex-1 text-left">{t(option.labelKey)}</span>
                {isSelected && <Check className="h-4 w-4" aria-hidden="true" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
