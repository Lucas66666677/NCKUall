"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  SlidersHorizontal,
} from "lucide-react";

import { useAppContext } from "@/components/AppContext";
import {
  SearchAutoComplete,
  type SearchSuggestion,
} from "@/components/SearchAutoComplete";
import { ThemeSwitcher } from "@/components/ThemeSwitcher";
import type {
  Course,
  GradeDistribution,
} from "@/lib/api-types";

type CourseFilter = "all" | "required" | "elective";

const CourseGradeChart = dynamic(
  () =>
    import("@/app/courses/CourseGradeChart").then(
      (module) => module.CourseGradeChart,
    ),
  {
    ssr: false,
    loading: () => <GradeChartSkeleton />,
  },
);

export function CoursesClient({
  initialCourses,
  initialDepartmentId,
}: {
  initialCourses: Course[];
  initialDepartmentId: string;
}) {
  const router = useRouter();
  const {
    currentDepartment,
    isDepartmentsLoading,
    departmentError,
    t,
  } = useAppContext();
  const filters: { value: CourseFilter; label: string }[] = [
    { value: "all", label: t("courses.filter.all") },
    { value: "required", label: t("courses.filter.required") },
    { value: "elective", label: t("courses.filter.elective") },
  ];
  const [courses, setCourses] = useState<Course[]>(initialCourses);
  const [loadedDepartmentId, setLoadedDepartmentId] = useState(
    initialDepartmentId,
  );
  const [searchTerm, setSearchTerm] = useState("");
  const [filter, setFilter] = useState<CourseFilter>("all");
  const [expandedCourseId, setExpandedCourseId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    const initialSearch = new URLSearchParams(window.location.search).get(
      "search",
    );
    if (initialSearch) {
      setSearchTerm(initialSearch);
    }
  }, []);

  useEffect(() => {
    if (isDepartmentsLoading) {
      return;
    }
    if (!currentDepartment.id) {
      setCourses([]);
      setIsLoading(false);
      setErrorMessage(
        departmentError ?? t("courses.noDepartment"),
      );
      return;
    }
    if (currentDepartment.id === loadedDepartmentId) {
      return;
    }

    const controller = new AbortController();

    async function fetchCourses() {
      setIsLoading(true);
      setErrorMessage(null);

      try {
        const params = new URLSearchParams({
          department_id: currentDepartment.id,
        });
        const response = await fetch(
          `/api/courses?${params.toString()}`,
          { signal: controller.signal },
        );

        if (!response.ok) {
          throw new Error(`Courses API failed with ${response.status}`);
        }

        const data = (await response.json()) as Course[];
        setCourses(data);
        setLoadedDepartmentId(currentDepartment.id);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setErrorMessage(t("courses.loadError"));
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      }
    }

    void fetchCourses();
    return () => controller.abort();
  }, [
    currentDepartment.id,
    departmentError,
    isDepartmentsLoading,
    loadedDepartmentId,
    t,
  ]);

  const filteredCourses = useMemo(() => {
    const keyword = searchTerm.trim().toLowerCase();

    return courses.filter((course) => {
      const matchesKeyword =
        !keyword ||
        course.title_zh.toLowerCase().includes(keyword) ||
        course.course_code.toLowerCase().includes(keyword) ||
        (course.instructor_name ?? "").toLowerCase().includes(keyword);

      const matchesFilter =
        filter === "all" ||
        (filter === "required" && course.required_for_major) ||
        (filter === "elective" && !course.required_for_major);

      return matchesKeyword && matchesFilter;
    });
  }, [courses, filter, searchTerm]);

  return (
    <main className="min-h-screen bg-mist dark:bg-[#081411]">
      <section className="border-b border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-950">
        <div className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="text-sm font-medium text-campus">{t("courses.eyebrow")}</p>
              <h1 className="mt-1 text-2xl font-bold tracking-normal text-ink dark:text-slate-100 sm:text-3xl">{t("courses.title")}</h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600 dark:text-slate-300">
                {t("courses.description")}
              </p>
            </div>

            <div className="flex items-center gap-2">
              <div className="rounded-md border border-campus/20 bg-white px-3 py-2 text-sm text-slate-600 dark:bg-slate-900 dark:text-slate-300">
                {t("courses.currentDepartment")}
                <span className="font-semibold text-campus dark:text-teal-300">
                  {currentDepartment.name}
                </span>
              </div>
              <ThemeSwitcher />
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 py-5 sm:px-6 lg:px-8">
        <div className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <SearchAutoComplete
            value={searchTerm}
            onChange={setSearchTerm}
            departmentId={currentDepartment.id}
            placeholder={t("courses.search.placeholder")}
            ariaLabel={t("courses.search.aria")}
            className="min-w-0 flex-1"
            onSelectSuggestion={(suggestion: SearchSuggestion) => {
              if (suggestion.resource_type === "event") {
                router.push(suggestion.href);
                return;
              }
              if (suggestion.resource_type === "course") {
                setExpandedCourseId(suggestion.resource_id);
              }
            }}
          />

          <div className="flex items-center gap-2 overflow-x-auto">
            <span className="flex h-10 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
              <SlidersHorizontal className="h-4 w-4" />
              {t("courses.filter")}
            </span>
            {filters.map((item) => (
              <button
                key={item.value}
                type="button"
                onClick={() => setFilter(item.value)}
                aria-pressed={filter === item.value}
                className={`h-10 shrink-0 rounded-md px-4 text-sm font-medium transition ${
                  filter === item.value
                    ? "bg-campus text-white shadow-sm"
                    : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {isLoading && (
          <CourseListSkeleton />
        )}

        {!isLoading && errorMessage && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm leading-6 text-red-700 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-300">
            {errorMessage}
          </div>
        )}

        {!isLoading && !errorMessage && filteredCourses.length === 0 && (
          <div className="rounded-lg border border-slate-200 bg-white p-6 text-center text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
            {t("courses.noResults")}
          </div>
        )}

        {!isLoading && !errorMessage && filteredCourses.length > 0 && (
          <div className="grid gap-4">
            {filteredCourses.map((course) => {
              const isExpanded = expandedCourseId === course.id;
              const hardness = getHardness(course, t);

              return (
                <article key={course.id} className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
                  <button
                    type="button"
                    onClick={() => setExpandedCourseId(isExpanded ? null : course.id)}
                    aria-expanded={isExpanded}
                    aria-controls={`course-detail-${course.id}`}
                    aria-label={t(isExpanded ? "courses.collapse" : "courses.expand", { title: course.title_zh })}
                    className="grid w-full gap-4 p-4 text-left transition hover:bg-slate-50 dark:hover:bg-slate-800/70 md:grid-cols-[minmax(0,1fr)_160px_130px_44px] md:items-center"
                  >
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                          {course.course_code}
                        </span>
                        <span className={`rounded-md px-2 py-1 text-xs font-semibold ${hardness.className}`}>
                          {hardness.label}
                        </span>
                        <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                          {course.required_for_major ? t("courses.required") : t("courses.elective")}
                        </span>
                      </div>
                      <h2 className="mt-2 truncate text-lg font-bold tracking-normal text-ink dark:text-slate-100">{course.title_zh}</h2>
                      {course.title_en && <p className="mt-1 truncate text-sm text-slate-500 dark:text-slate-400">{course.title_en}</p>}
                    </div>

                    <div>
                      <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{t("courses.instructor")}</p>
                      <p className="mt-1 text-sm font-semibold text-ink dark:text-slate-100">{course.instructor_name ?? t("courses.notProvided")}</p>
                    </div>

                    <div>
                      <p className="text-xs font-medium text-slate-500 dark:text-slate-400">{t("courses.credits")}</p>
                      <p className="mt-1 text-sm font-semibold text-ink dark:text-slate-100">{formatCredits(course.credits, t("courses.notProvided"))}</p>
                    </div>

                    <span className="flex h-11 w-11 items-center justify-center rounded-md border border-slate-200 text-slate-500 dark:border-slate-700 dark:text-slate-300 md:justify-self-end">
                      {isExpanded ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
                    </span>
                  </button>

                  {isExpanded && <CourseDetailPanel course={course} panelId={`course-detail-${course.id}`} />}
                </article>
              );
            })}
          </div>
        )}
      </section>
    </main>
  );
}

function CourseListSkeleton() {
  const { t } = useAppContext();
  return (
    <div
      className="grid gap-4"
      aria-label={t("courses.loading.aria")}
      aria-busy="true"
    >
      {Array.from({ length: 5 }, (_, index) => (
        <div
          key={index}
          className="grid min-h-32 animate-pulse gap-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900 md:grid-cols-[minmax(0,1fr)_160px_130px_44px] md:items-center"
        >
          <div className="min-w-0">
            <div className="flex gap-2">
              <span className="h-6 w-20 rounded-md bg-slate-200 dark:bg-slate-700" />
              <span className="h-6 w-14 rounded-md bg-slate-100 dark:bg-slate-800" />
            </div>
            <div className="mt-3 h-5 w-2/3 max-w-sm rounded bg-slate-200 dark:bg-slate-700" />
            <div className="mt-2 h-4 w-1/3 max-w-48 rounded bg-slate-100 dark:bg-slate-800" />
          </div>
          <div>
            <div className="h-3 w-12 rounded bg-slate-100 dark:bg-slate-800" />
            <div className="mt-2 h-5 w-24 rounded bg-slate-200 dark:bg-slate-700" />
          </div>
          <div>
            <div className="h-3 w-12 rounded bg-slate-100 dark:bg-slate-800" />
            <div className="mt-2 h-7 w-20 rounded-md bg-slate-200 dark:bg-slate-700" />
          </div>
          <div className="hidden h-10 w-10 rounded-md bg-slate-100 dark:bg-slate-800 md:block" />
        </div>
      ))}
    </div>
  );
}

function GradeChartSkeleton() {
  const { t } = useAppContext();
  return (
    <div
      className="flex h-72 w-full animate-pulse items-end gap-2 rounded-md border border-slate-200 bg-slate-50 px-4 py-5 dark:border-slate-800 dark:bg-slate-950/60 sm:h-80"
      aria-label={t("courses.chart.loading")}
    >
      {Array.from({ length: 10 }, (_, index) => (
        <div
          key={index}
          className="flex flex-1 items-end"
          style={{ height: `${36 + ((index * 17) % 54)}%` }}
        >
          <div className="h-full w-full rounded-t bg-slate-200 dark:bg-slate-700" />
        </div>
      ))}
    </div>
  );
}

function CourseDetailPanel({ course, panelId }: { course: Course; panelId: string }) {
  const { t } = useAppContext();
  const latestDistribution = [...course.grade_distributions].sort(
    (a, b) => b.academic_year - a.academic_year || b.semester - a.semester,
  )[0];

  return (
    <div id={panelId} className="border-t border-slate-200 px-4 py-5 dark:border-slate-800">
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <section className="min-w-0">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <h3 className="text-base font-bold text-ink dark:text-slate-100">{t("courses.gradeDistribution")}</h3>
              <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{t("courses.gradeDistribution.description")}</p>
            </div>
          </div>

          <CourseGradeChart distributions={course.grade_distributions} />
        </section>

        <aside className="border-t border-slate-200 pt-4 dark:border-slate-800 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
          <h3 className="text-base font-bold text-ink dark:text-slate-100">{t("courses.hardnessSummary")}</h3>
          <dl className="mt-3 space-y-3 text-sm">
            <div>
              <dt className="text-slate-500 dark:text-slate-400">{t("courses.enrollment")}</dt>
              <dd className="mt-1 font-semibold text-ink dark:text-slate-100">{latestDistribution?.enrollment_count ?? t("courses.notProvided")}</dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">{t("courses.passRate")}</dt>
              <dd className="mt-1 font-semibold text-ink dark:text-slate-100">{formatPercent(latestDistribution?.pass_rate, t("courses.notProvided"))}</dd>
            </div>
            <div>
              <dt className="text-slate-500 dark:text-slate-400">{t("courses.dataCount")}</dt>
              <dd className="mt-1 font-semibold text-ink dark:text-slate-100">{course.grade_distributions.length}</dd>
            </div>
          </dl>
          {course.description && <p className="mt-4 text-sm leading-6 text-slate-600 dark:text-slate-300">{course.description}</p>}
          <Link
            href={`/courses/${course.id}`}
            className="mt-4 inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-campus px-4 text-sm font-semibold text-white transition hover:bg-campus/90"
          >
            <ExternalLink className="h-4 w-4" aria-hidden="true" />
            {t("courses.viewDetail")}
          </Link>
        </aside>
      </div>
    </div>
  );
}

function getHardness(course: Course, t: ReturnType<typeof useAppContext>["t"]) {
  const latestFailRate = getAverageFailRate(course.grade_distributions);
  if (course.difficulty === "hard" || latestFailRate >= 12) {
    return { label: t("courses.hardness.high"), className: "bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-300" };
  }
  if (course.difficulty === "medium" || latestFailRate >= 6) {
    return { label: t("courses.hardness.medium"), className: "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300" };
  }
  if (course.difficulty === "easy" || latestFailRate > 0) {
    return { label: t("courses.hardness.low"), className: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300" };
  }
  return { label: t("courses.hardness.pending"), className: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300" };
}

function getAverageFailRate(distributions: GradeDistribution[]) {
  const failRates = distributions
    .map((distribution) => {
      const bucketFail = distribution.grade_buckets?.fail_ratio ?? distribution.grade_buckets?.F_ratio;
      if (bucketFail !== undefined && bucketFail !== null) {
        return Number(bucketFail) * 100;
      }

      if (distribution.pass_rate !== null && distribution.pass_rate !== undefined) {
        return (1 - Number(distribution.pass_rate)) * 100;
      }

      return null;
    })
    .filter((value): value is number => value !== null && Number.isFinite(value));

  if (failRates.length === 0) {
    return 0;
  }

  return failRates.reduce((sum, value) => sum + value, 0) / failRates.length;
}

function formatCredits(value: Course["credits"], fallback: string) {
  if (value === null || value === undefined) {
    return fallback;
  }
  return `${Number(value).toLocaleString("zh-TW")} 學分`;
}

function formatPercent(value: GradeDistribution["pass_rate"] | undefined, fallback: string) {
  if (value === null || value === undefined) {
    return fallback;
  }
  return `${(Number(value) * 100).toFixed(1)}%`;
}
