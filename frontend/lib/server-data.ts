import "server-only";

import type {
  Course,
  DepartmentApiResponse,
  LifeReview,
  LifeReviewType,
} from "@/lib/api-types";
import { getServerApiBaseUrl } from "@/lib/server-runtime-config";

export const DAY_IN_SECONDS = 86_400;
export const CACHE_TAGS = {
  departments: "departments",
  courses: "courses",
  lifeReviews: "life-reviews",
} as const;

function getApiBaseUrl() {
  return getServerApiBaseUrl();
}

async function fetchCachedJson<T>(
  pathname: string,
  tags: string[],
): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${pathname}`, {
    next: {
      revalidate: DAY_IN_SECONDS,
      tags,
    },
    headers: {
      Accept: "application/json",
    },
  });

  if (!response.ok) {
    throw new Error(
      `Upstream API ${pathname} returned HTTP ${response.status}`,
    );
  }

  return (await response.json()) as T;
}

async function withLocalFallback<T>(
  operation: () => Promise<T>,
  fallback: T,
  resourceName: string,
): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    console.warn(
      `[server-data] ${resourceName} unavailable; using fallback. ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
    return fallback;
  }
}

export function getDepartments(): Promise<DepartmentApiResponse[]> {
  return withLocalFallback(
    () =>
      fetchCachedJson<DepartmentApiResponse[]>("/api/departments", [
        CACHE_TAGS.departments,
      ]),
    [],
    "departments",
  );
}

export function getCourses(departmentId: string): Promise<Course[]> {
  const encodedDepartmentId = encodeURIComponent(departmentId);
  return withLocalFallback(
    () =>
      fetchCachedJson<Course[]>(
        `/api/courses?department_id=${encodedDepartmentId}`,
        [
          CACHE_TAGS.courses,
          `${CACHE_TAGS.courses}:${departmentId}`,
        ],
      ),
    [],
    `courses:${departmentId}`,
  );
}

export function getLifeReviews(
  reviewType?: LifeReviewType,
): Promise<LifeReview[]> {
  const params = new URLSearchParams({ limit: "50" });
  if (reviewType) {
    params.set("review_type", reviewType);
  }

  return withLocalFallback(
    () =>
      fetchCachedJson<LifeReview[]>(
        `/api/life/reviews?${params.toString()}`,
        [CACHE_TAGS.lifeReviews],
      ),
    [],
    reviewType ? `life-reviews:${reviewType}` : "life-reviews",
  );
}

export function selectDefaultDepartment(
  departments: DepartmentApiResponse[],
) {
  const configuredId = process.env.NEXT_PUBLIC_DEFAULT_DEPARTMENT_ID;
  return (
    departments.find((department) => department.id === configuredId) ??
    departments.find((department) => department.code === "DPS") ??
    departments[0] ??
    null
  );
}
