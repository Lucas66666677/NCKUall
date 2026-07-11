"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  BriefcaseBusiness,
  ExternalLink,
  FlaskConical,
  GraduationCap,
  Plane,
  Search,
  Star,
  UserRound,
} from "lucide-react";

import { AIAssistantSidebar } from "@/components/AIAssistant";
import { useAppContext } from "@/components/AppContext";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

type CareerResourceType =
  | "exchange"
  | "study_abroad"
  | "grad_school"
  | "lab_review"
  | "pre_master"
  | "transfer_department"
  | "program"
  | "other";

type CareerResource = {
  id: string;
  department_id: string;
  resource_type: CareerResourceType;
  title: string;
  organization_name: string | null;
  professor_name: string | null;
  location: string | null;
  summary: string | null;
  requirements: string | null;
  application_timeline: string | null;
  official_url: string | null;
  source_url: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
};

type CareerTab = {
  id: string;
  label: string;
  category: string;
  icon: typeof FlaskConical;
};

const tabs: CareerTab[] = [
  { id: "labs", label: "實驗室", category: "實驗室", icon: FlaskConical },
  { id: "exchange", label: "海外交換", category: "海外交換", icon: Plane },
  { id: "dual-degree", label: "雙聯學位", category: "雙聯學位", icon: GraduationCap },
  { id: "pre-master", label: "預研", category: "預研", icon: BriefcaseBusiness },
  { id: "grad-school", label: "推甄", category: "推甄", icon: Star },
];

export default function CareersPage() {
  const {
    currentDepartment,
    setCurrentDepartment,
    departments,
    isDepartmentsLoading,
    departmentError,
    t,
  } = useAppContext();
  const [activeTabId, setActiveTabId] = useState("labs");
  const [resources, setResources] = useState<CareerResource[]>([]);
  const [searchTerm, setSearchTerm] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isAssistantOpen, setIsAssistantOpen] = useState(false);
  const [quickPrompt, setQuickPrompt] = useState<string | null>(null);

  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? tabs[0];

  useEffect(() => {
    if (isDepartmentsLoading) {
      return;
    }
    if (!currentDepartment.id) {
      setResources([]);
      setIsLoading(false);
      setErrorMessage(
        departmentError ?? t("careers.noDepartment"),
      );
      return;
    }

    const controller = new AbortController();

    async function fetchCareerResources() {
      setIsLoading(true);
      setErrorMessage(null);

      try {
        const params = new URLSearchParams({
          category: activeTab.category,
          department_id: currentDepartment.id,
        });
        const response = await fetch(
          `${API_BASE_URL}/api/careers?${params.toString()}`,
          { signal: controller.signal },
        );

        if (!response.ok) {
          throw new Error(`Career API failed with ${response.status}`);
        }

        const data = (await response.json()) as CareerResource[];
        setResources(data);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setErrorMessage(t("careers.loadError"));
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      }
    }

    void fetchCareerResources();
    return () => controller.abort();
  }, [
    activeTab.category,
    currentDepartment.id,
    departmentError,
    isDepartmentsLoading,
    t,
  ]);

  const filteredResources = useMemo(() => {
    const keyword = searchTerm.trim().toLowerCase();
    if (!keyword) {
      return resources;
    }

    return resources.filter((resource) => {
      const searchable = [
        resource.title,
        resource.professor_name,
        resource.organization_name,
        resource.summary,
        resource.requirements,
        resource.tags.join(" "),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      return searchable.includes(keyword);
    });
  }, [resources, searchTerm]);

  function askAboutProfessor(professorName: string) {
    const prompt = t("careers.quickPrompt", { professor: professorName });
    setQuickPrompt(prompt);
    setIsAssistantOpen(true);
  }

  const isLabTab = activeTab.id === "labs";

  return (
    <main className="min-h-screen bg-mist pb-24 dark:bg-[#081411]">
      <section className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
        <div className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="text-sm font-medium text-campus">{t("careers.eyebrow")}</p>
              <h1 className="mt-1 text-2xl font-bold tracking-normal text-ink dark:text-slate-100 sm:text-3xl">{t("careers.title")}</h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600 dark:text-slate-300">
                {t("careers.description")}
              </p>
            </div>

            <div className="flex items-center gap-2">
              <label className="sr-only" htmlFor="career-department">
                {t("careers.selectDepartment")}
              </label>
              <select
                id="career-department"
                aria-label={t("careers.selectDepartment")}
                value={currentDepartment.id}
                disabled={isDepartmentsLoading}
                onChange={(event) => {
                  const department = departments.find(
                    (item) => item.id === event.target.value,
                  );
                  if (department) {
                    setCurrentDepartment(department);
                  }
                }}
                className="h-10 max-w-[260px] rounded-md border border-campus/20 bg-white px-3 text-sm font-semibold text-campus outline-none focus:border-campus focus:ring-4 focus:ring-campus/15 dark:bg-slate-900 dark:text-teal-300"
              >
                {departments.map((department) => (
                  <option key={department.id} value={department.id}>
                    {department.name}
                  </option>
                ))}
              </select>
              <ThemeSwitcher />
            </div>
          </div>

          <div className="mt-5 flex gap-2 overflow-x-auto pb-1" aria-label={t("careers.tabs.aria")} role="tablist">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTabId === tab.id;

              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTabId(tab.id)}
                  role="tab"
                  aria-selected={isActive}
                  aria-controls="career-resource-panel"
                  className={`flex h-11 shrink-0 items-center gap-2 rounded-md px-4 text-sm font-semibold transition ${
                    isActive
                      ? "bg-campus text-white shadow-sm"
                      : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 hover:text-ink dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white"
                  }`}
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {getCareerTabLabel(tab.id, t)}
                </button>
              );
            })}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
        <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative w-full sm:max-w-md">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder={isLabTab ? "搜尋教授、研究領域或實驗室" : "搜尋計畫、學校或申請條件"}
              aria-label={isLabTab ? t("careers.search.labs") : t("careers.search.general")}
              className="h-11 w-full rounded-md border border-slate-300 bg-white pl-10 pr-3 text-sm text-ink outline-none focus:border-campus focus:ring-4 focus:ring-campus/15 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500"
            />
          </div>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            {isLoading ? t("careers.loading") : t("careers.count", { count: filteredResources.length, category: getCareerTabLabel(activeTab.id, t) })}
          </p>
        </div>

        {isLoading && (
          <CareerSkeletonGrid isLabTab={isLabTab} />
        )}

        {!isLoading && errorMessage && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm leading-6 text-red-700 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-300">
            {errorMessage}
          </div>
        )}

        {!isLoading && !errorMessage && filteredResources.length === 0 && (
          <div className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
            {t("careers.noResults")}
          </div>
        )}

        {!isLoading && !errorMessage && filteredResources.length > 0 && (
          <div id="career-resource-panel" role="tabpanel" aria-label={getCareerTabLabel(activeTab.id, t)}>
            {isLabTab ? (
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {filteredResources.map((resource) => (
                  <ProfessorCard
                    key={resource.id}
                    resource={resource}
                    onAskAI={askAboutProfessor}
                  />
                ))}
              </div>
            ) : (
              <div className="grid gap-4 lg:grid-cols-2">
                {filteredResources.map((resource) => (
                  <CareerResourceCard key={resource.id} resource={resource} />
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      <AIAssistantSidebar
        open={isAssistantOpen}
        departmentFilter={currentDepartment.name}
        autoPrompt={quickPrompt}
        onAutoPromptConsumed={() => setQuickPrompt(null)}
        onOpenChange={setIsAssistantOpen}
      />
    </main>
  );
}

function CareerSkeletonGrid({ isLabTab }: { isLabTab: boolean }) {
  const { t } = useAppContext();
  return (
    <div
      className={`grid gap-4 ${
        isLabTab ? "md:grid-cols-2 xl:grid-cols-3" : "lg:grid-cols-2"
      }`}
      aria-label={t("careers.loading.aria")}
      aria-busy="true"
    >
      {Array.from({ length: isLabTab ? 6 : 4 }, (_, index) => (
        <div
          key={index}
          className="min-h-64 animate-pulse rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900"
        >
          <div className="flex items-start gap-3">
            <div className="h-12 w-12 shrink-0 rounded-lg bg-slate-200 dark:bg-slate-700" />
            <div className="min-w-0 flex-1 pt-1">
              <div className="h-5 w-32 rounded bg-slate-200 dark:bg-slate-700" />
              <div className="mt-2 h-4 w-3/4 rounded bg-slate-100 dark:bg-slate-800" />
            </div>
          </div>
          <div className="mt-5 flex gap-2">
            <span className="h-6 w-20 rounded-md bg-slate-200 dark:bg-slate-700" />
            <span className="h-6 w-16 rounded-md bg-slate-100 dark:bg-slate-800" />
            <span className="h-6 w-24 rounded-md bg-slate-100 dark:bg-slate-800" />
          </div>
          <div className="mt-5 space-y-2">
            <div className="h-4 w-full rounded bg-slate-100 dark:bg-slate-800" />
            <div className="h-4 w-11/12 rounded bg-slate-100 dark:bg-slate-800" />
            <div className="h-4 w-2/3 rounded bg-slate-100 dark:bg-slate-800" />
          </div>
          <div className="mt-5 h-10 w-full rounded-md bg-slate-200 dark:bg-slate-700" />
        </div>
      ))}
    </div>
  );
}

function ProfessorCard({
  resource,
  onAskAI,
}: {
  resource: CareerResource;
  onAskAI: (professorName: string) => void;
}) {
  const { t } = useAppContext();
  const professorName = getProfessorName(resource);
  const researchTags = getResearchTags(resource);

  return (
    <article className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start gap-3">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-campus/10 text-campus">
          <UserRound className="h-6 w-6" aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-lg font-bold tracking-normal text-ink dark:text-slate-100">{professorName}</h2>
          <p className="mt-1 truncate text-sm text-slate-500 dark:text-slate-400">{resource.title}</p>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {researchTags.length > 0 ? (
          researchTags.slice(0, 6).map((tag) => (
            <span key={tag} className="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              {tag}
            </span>
          ))
        ) : (
          <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">{t("careers.researchPending")}</span>
        )}
      </div>

      {resource.summary && <p className="mt-4 line-clamp-3 text-sm leading-6 text-slate-600 dark:text-slate-300">{resource.summary}</p>}

      <div className="mt-4 flex items-center gap-2">
        <button
          type="button"
          onClick={() => onAskAI(professorName)}
          aria-label={t("careers.askAI.aria", { professor: professorName })}
          className="inline-flex h-10 flex-1 items-center justify-center gap-2 rounded-md bg-campus px-3 text-sm font-semibold text-white transition hover:bg-campus/90"
        >
          <Bot className="h-4 w-4" aria-hidden="true" />
          {t("careers.askAI")}
        </button>
        {(resource.official_url || resource.source_url) && (
          <a
            href={resource.official_url ?? resource.source_url ?? undefined}
            target="_blank"
            rel="noreferrer"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-slate-200 text-slate-500 hover:bg-slate-50 hover:text-ink dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white"
            aria-label={t("careers.source")}
          >
            <ExternalLink className="h-4 w-4" aria-hidden="true" />
          </a>
        )}
      </div>
    </article>
  );
}

function CareerResourceCard({ resource }: { resource: CareerResource }) {
  const { t } = useAppContext();
  return (
    <article className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-bold tracking-normal text-ink dark:text-slate-100">{resource.title}</h2>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            {resource.organization_name ?? resource.location ?? t("careers.sourcePending")}
          </p>
        </div>
        {(resource.official_url || resource.source_url) && (
          <a
            href={resource.official_url ?? resource.source_url ?? undefined}
            target="_blank"
            rel="noreferrer"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-slate-200 text-slate-500 hover:bg-slate-50 hover:text-ink dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white"
            aria-label={t("careers.source")}
          >
            <ExternalLink className="h-4 w-4" aria-hidden="true" />
          </a>
        )}
      </div>

      {resource.summary && <p className="mt-3 text-sm leading-6 text-slate-600 dark:text-slate-300">{resource.summary}</p>}

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <InfoBlock label={t("careers.requirements")} value={resource.requirements} />
        <InfoBlock label={t("careers.timeline")} value={resource.application_timeline} />
      </div>

      {resource.tags.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {resource.tags.map((tag) => (
            <span key={tag} className="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              {tag}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

function InfoBlock({ label, value }: { label: string; value: string | null }) {
  const { t } = useAppContext();
  return (
    <div className="rounded-md bg-mist p-3 dark:bg-slate-950/70">
      <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 line-clamp-3 text-sm leading-6 text-slate-700 dark:text-slate-300">{value ?? t("careers.pending")}</p>
    </div>
  );
}

function getCareerTabLabel(
  id: string,
  t: ReturnType<typeof useAppContext>["t"],
) {
  const labels = {
    labs: "careers.tab.labs",
    exchange: "careers.tab.exchange",
    "dual-degree": "careers.tab.dualDegree",
    "pre-master": "careers.tab.preMaster",
    "grad-school": "careers.tab.gradSchool",
  } as const;

  return t(labels[id as keyof typeof labels] ?? "careers.tab.labs");
}

function getProfessorName(resource: CareerResource) {
  if (resource.professor_name) {
    return resource.professor_name;
  }

  const titleMatch = resource.title.match(/([\u4e00-\u9fff]{2,4})(?:教授|老師|實驗室)?/);
  return titleMatch?.[1] ?? resource.title;
}

function getResearchTags(resource: CareerResource) {
  if (resource.tags.length > 0) {
    return resource.tags;
  }

  const text = [resource.summary, resource.requirements, resource.title].filter(Boolean).join("、");
  return text
    .split(/[、,，/／;\s]+/)
    .map((tag) => tag.trim())
    .filter((tag) => tag.length >= 2 && tag.length <= 12)
    .slice(0, 6);
}
