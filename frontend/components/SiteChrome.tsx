"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BriefcaseBusiness,
  CalendarDays,
  LibraryBig,
  Utensils,
} from "lucide-react";

import { AuthStatusButton } from "@/components/AuthStatusButton";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { LucirelProductBrand } from "@/components/LucirelProductBrand";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { useAppContext } from "@/components/AppContext";

const navItems = [
  { href: "/courses", labelKey: "nav.courses", icon: LibraryBig },
  { href: "/careers", labelKey: "nav.careers", icon: BriefcaseBusiness },
  { href: "/events", labelKey: "nav.events", icon: CalendarDays },
  { href: "/life", labelKey: "nav.life", icon: Utensils },
] as const;

export function SiteChrome() {
  const pathname = usePathname();
  const { t } = useAppContext();

  if (pathname === "/") {
    return null;
  }

  return (
    <header className="sticky top-0 z-40 border-b border-white/60 bg-white/85 shadow-sm backdrop-blur-md dark:border-slate-800/80 dark:bg-slate-950/85">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
        <Link href="/" className="flex min-w-0 items-center gap-3">
          <LucirelProductBrand />
        </Link>

        <nav
          className="hidden items-center gap-1 lg:flex"
          aria-label={t("nav.aria.main")}
        >
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex h-10 items-center gap-2 rounded-md px-3 text-sm font-medium transition ${
                  isActive
                    ? "bg-campus text-white shadow-sm"
                    : "text-slate-600 hover:bg-slate-100 hover:text-ink dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white"
                }`}
              >
                <Icon className="h-4 w-4" aria-hidden="true" />
                {t(item.labelKey)}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-2">
          <div className="hidden sm:block">
            <AuthStatusButton />
          </div>
          <div className="sm:hidden">
            <AuthStatusButton compact />
          </div>
          <ThemeSwitcher />
          <LanguageSwitcher />
        </div>
      </div>

      <nav
        className="flex gap-2 overflow-x-auto border-t border-slate-200 px-4 py-2 dark:border-slate-800 lg:hidden"
        aria-label={t("nav.aria.mobile")}
      >
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex h-10 shrink-0 items-center gap-2 rounded-md px-3 text-sm font-semibold ${
                isActive
                  ? "bg-campus text-white"
                  : "border border-slate-200 bg-white text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
              }`}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              {t(item.labelKey)}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
