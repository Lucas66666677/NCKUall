"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { useAppContext } from "@/components/AppContext";

const themeOptions = [
  { value: "light", labelKey: "theme.light", icon: Sun },
  { value: "dark", labelKey: "theme.dark", icon: Moon },
  { value: "system", labelKey: "theme.system", icon: Monitor },
] as const;

export function ThemeSwitcher() {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const { t } = useAppContext();
  const [mounted, setMounted] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

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

  const selectedTheme = mounted ? theme ?? "system" : "system";
  const isDark = mounted && resolvedTheme === "dark";

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        className="relative flex h-10 w-10 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-700 shadow-sm transition hover:bg-slate-50 focus:outline-none focus:ring-4 focus:ring-campus/15 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800"
        onClick={() => setIsOpen((open) => !open)}
        aria-label={t("theme.switcher")}
        aria-haspopup="menu"
        aria-expanded={isOpen}
      >
        <Sun
          className={`absolute h-5 w-5 transition duration-300 ${
            isDark ? "rotate-90 scale-0 opacity-0" : "rotate-0 scale-100 opacity-100"
          }`}
          aria-hidden="true"
        />
        <Moon
          className={`absolute h-5 w-5 transition duration-300 ${
            isDark ? "rotate-0 scale-100 opacity-100" : "-rotate-90 scale-0 opacity-0"
          }`}
          aria-hidden="true"
        />
      </button>

      {mounted && isOpen && (
        <div
          className="absolute right-0 top-12 z-50 w-40 overflow-hidden rounded-lg border border-white/70 bg-white/90 p-1.5 shadow-xl backdrop-blur-md dark:border-slate-700/80 dark:bg-slate-900/90"
          role="menu"
          aria-label={t("theme.menu")}
        >
          {themeOptions.map((option) => {
            const Icon = option.icon;
            const isSelected = selectedTheme === option.value;

            return (
              <button
                key={option.value}
                type="button"
                className={`flex h-10 w-full items-center gap-2 rounded-md px-2.5 text-sm transition ${
                  isSelected
                    ? "bg-campus text-white"
                    : "text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800"
                }`}
                onClick={() => {
                  setTheme(option.value);
                  setIsOpen(false);
                }}
                role="menuitemradio"
                aria-checked={isSelected}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
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
