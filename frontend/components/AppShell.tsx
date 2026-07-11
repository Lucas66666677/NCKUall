"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  BriefcaseBusiness,
  CalendarDays,
  ChevronDown,
  Home,
  LibraryBig,
  Menu,
  Sparkles,
  Utensils,
  X,
} from "lucide-react";

import { AIAssistantSidebar } from "@/components/AIAssistant";
import {
  type Department,
  useAppContext,
} from "@/components/AppContext";
import { NckuReviewComposer } from "@/components/NckuReviewComposer";
import { SearchAutoComplete } from "@/components/SearchAutoComplete";
import { RecommendationCarousel } from "@/components/RecommendationCarousel";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import { TrendingPanel } from "@/components/TrendingPanel";

type SectionId = "courses" | "careers" | "events" | "life";

type NavItem = {
  id: SectionId;
  label: string;
  description: string;
  icon: typeof LibraryBig;
};

const previewRows: Record<SectionId, string[]> = {
  courses: ["資料結構", "普通物理", "工程數學", "經濟學原理"],
  careers: ["實驗室評價", "交換申請", "推甄準備", "預研制度"],
  events: ["社團博覽會", "校園講座", "單車節", "畢業舞會"],
  life: ["東寧路美食", "勝利校區租屋", "自習空間", "機車停車資訊"],
};

export function AppShell() {
  const router = useRouter();
  const {
    currentDepartment,
    setCurrentDepartment,
    departments,
    isDepartmentsLoading,
    departmentError,
    t,
  } = useAppContext();
  const navItems: NavItem[] = [
    {
      id: "courses",
      label: t("nav.courses"),
      description: t("nav.courses.description"),
      icon: LibraryBig,
    },
    {
      id: "careers",
      label: t("nav.careers"),
      description: t("nav.careers.description"),
      icon: BriefcaseBusiness,
    },
    {
      id: "events",
      label: t("nav.events"),
      description: t("nav.events.description"),
      icon: CalendarDays,
    },
    {
      id: "life",
      label: t("nav.life"),
      description: t("nav.life.description"),
      icon: Utensils,
    },
  ];
  const activeSection = navItems[0].id;
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);
  const [isAssistantOpen, setIsAssistantOpen] = useState(false);
  const [homeSearch, setHomeSearch] = useState("");

  const activeNav = navItems[0];
  const shouldEmphasizeDepartment = activeSection === "courses" || activeSection === "careers";

  return (
    <div className="min-h-screen pb-24">
      <header className="sticky top-0 z-30 border-b border-white/60 bg-white/80 shadow-sm backdrop-blur-md dark:border-slate-800/80 dark:bg-slate-950/80">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
          <Link
            href="/"
            className="flex min-w-0 items-center gap-3 text-left"
          >
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-campus text-white shadow-sm">
              <Home className="h-5 w-5" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-base font-bold tracking-normal text-ink dark:text-slate-100">NCKU Hub</span>
              <span className="hidden text-xs text-slate-500 dark:text-slate-400 sm:block">{t("app.logoSubtitle")}</span>
            </span>
          </Link>

          <nav className="hidden items-center gap-1 lg:flex" aria-label={t("nav.aria.main")}>
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = activeSection === item.id;
              return (
                <Link
                  key={item.id}
                  href={`/${item.id}`}
                  className={`flex h-10 items-center gap-2 rounded-md px-3 text-sm font-medium transition ${
                    isActive
                      ? "bg-campus text-white shadow-sm"
                      : "text-slate-600 hover:bg-slate-100 hover:text-ink dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white"
                  }`}
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="flex items-center gap-2">
            <ThemeSwitcher />
            <LanguageSwitcher />
            <button
              type="button"
              className="flex h-10 w-10 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 lg:hidden"
              onClick={() => setIsMobileNavOpen((open) => !open)}
              aria-label={isMobileNavOpen ? t("nav.close") : t("nav.open")}
              aria-expanded={isMobileNavOpen}
              aria-controls="mobile-navigation"
            >
              {isMobileNavOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
          </div>
        </div>

        {isMobileNavOpen && (
          <nav
            id="mobile-navigation"
            className="border-t border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-950 lg:hidden"
            aria-label={t("nav.aria.mobile")}
          >
            <div className="grid gap-2">
              {navItems.map((item) => {
                const Icon = item.icon;
                const isActive = activeSection === item.id;
                return (
                  <Link
                    key={item.id}
                    href={`/${item.id}`}
                    onClick={() => setIsMobileNavOpen(false)}
                    className={`flex items-center gap-3 rounded-md px-3 py-3 text-left ${
                      isActive ? "bg-campus text-white" : "bg-slate-50 text-slate-700 dark:bg-slate-900 dark:text-slate-200"
                    }`}
                  >
                    <Icon className="h-5 w-5 shrink-0" />
                    <span>
                      <span className="block text-sm font-semibold">{item.label}</span>
                      <span className={`block text-xs ${isActive ? "text-white/80" : "text-slate-500 dark:text-slate-400"}`}>
                        {item.description}
                      </span>
                    </span>
                  </Link>
                );
              })}
            </div>
          </nav>
        )}
      </header>

      <main className="mx-auto grid max-w-7xl gap-6 px-4 py-5 sm:px-6 lg:grid-cols-[260px_minmax(0,1fr)] lg:px-8 xl:grid-cols-[240px_minmax(0,1fr)_300px]">
        <aside className="lg:sticky lg:top-20 lg:self-start">
          <DepartmentSelector
            currentDepartment={currentDepartment}
            departments={departments}
            isLoading={isDepartmentsLoading}
            errorMessage={departmentError}
            emphasize={shouldEmphasizeDepartment}
            onChange={setCurrentDepartment}
          />
        </aside>

        <section className="min-w-0">
          <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-sm font-medium text-campus">{currentDepartment.college}</p>
              <h1 className="mt-1 text-2xl font-bold tracking-normal text-ink dark:text-slate-100 sm:text-3xl">
                {activeNav.label}
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600 dark:text-slate-300 sm:text-base">
                {activeNav.description}
                {shouldEmphasizeDepartment ? `，${t("home.currentFilter", { department: currentDepartment.name })}` : ""}
              </p>
            </div>
            <div className="flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
              <Sparkles className="h-4 w-4 text-brick" aria-hidden="true" />
              {t("home.aiNote")}
            </div>
          </div>

          <SearchAutoComplete
            value={homeSearch}
            onChange={setHomeSearch}
            departmentId={
              shouldEmphasizeDepartment ? currentDepartment.id : null
            }
            placeholder={t("home.search.placeholder")}
            ariaLabel={t("home.search.aria")}
            className="mb-5"
            onSubmit={(keyword) => {
              router.push(
                `/courses?search=${encodeURIComponent(keyword)}`,
              );
            }}
          />

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            {previewRows[activeSection].map((title, index) => (
              <article key={title} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold text-ink dark:text-slate-100">{title}</h2>
                    <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-slate-300">
                      {activeSection === "courses" && t("home.card.courses")}
                      {activeSection === "careers" && t("home.card.careers")}
                      {activeSection === "events" && t("home.card.events")}
                      {activeSection === "life" && t("home.card.life")}
                    </p>
                  </div>
                  <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    #{index + 1}
                  </span>
                </div>
              </article>
            ))}
          </div>

          <RecommendationCarousel />

          {activeSection === "life" && <NckuReviewComposer />}
        </section>

        <aside className="lg:col-start-2 xl:col-start-3 xl:row-start-1 xl:sticky xl:top-20 xl:self-start">
          <TrendingPanel />
        </aside>
      </main>

      <AIAssistantSidebar
        open={isAssistantOpen}
        departmentFilter={currentDepartment.name}
        onOpenChange={setIsAssistantOpen}
      />
    </div>
  );
}

function DepartmentSelector({
  currentDepartment,
  departments,
  isLoading,
  errorMessage,
  emphasize,
  onChange,
}: {
  currentDepartment: Department;
  departments: Department[];
  isLoading: boolean;
  errorMessage: string | null;
  emphasize: boolean;
  onChange: (department: Department) => void;
}) {
  const { t } = useAppContext();
  return (
    <section
      className={`rounded-lg border bg-white/80 p-4 shadow-sm backdrop-blur-md transition dark:bg-slate-900/80 ${
        emphasize ? "border-campus ring-4 ring-campus/10" : "border-slate-200 dark:border-slate-800"
      }`}
      aria-label={t("department.selector.aria")}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className={`text-xs font-semibold uppercase ${emphasize ? "text-campus" : "text-slate-500"}`}>
            {t("department.selector.eyebrow")}
          </p>
          <h2 className="mt-1 text-lg font-bold tracking-normal text-ink dark:text-slate-100">{t("department.selector.title")}</h2>
        </div>
        {emphasize && (
          <span className="rounded-md bg-campus px-2 py-1 text-xs font-semibold text-white">{t("department.selector.important")}</span>
        )}
      </div>

      <label className="mt-4 block text-sm font-medium text-slate-700 dark:text-slate-300" htmlFor="department">
        {t("department.selector.current")}
      </label>
      <div className="relative mt-2">
        <select
          id="department"
          value={currentDepartment.id}
          disabled={isLoading}
          onChange={(event) => {
            const department = departments.find(
              (item) => item.id === event.target.value,
            );
            if (department) {
              onChange(department);
            }
          }}
          className="h-12 w-full appearance-none rounded-md border border-slate-300 bg-white px-3 pr-10 text-sm font-medium text-ink outline-none transition focus:border-campus focus:ring-4 focus:ring-campus/15 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
        >
          {departments.map((department) => (
            <option key={department.id} value={department.id}>
              {department.name}
            </option>
          ))}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500"
          aria-hidden="true"
        />
      </div>

      <div className="mt-4 rounded-md bg-mist p-3 dark:bg-slate-950/70">
        <p className="text-sm font-semibold text-ink dark:text-slate-100">
          {isLoading ? t("department.selector.loading") : currentDepartment.name}
        </p>
        <p className="mt-1 text-xs leading-5 text-slate-600 dark:text-slate-400">
          {errorMessage ??
            t("department.selector.helper")}
        </p>
      </div>
    </section>
  );
}
