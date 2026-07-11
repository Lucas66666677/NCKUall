"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import { createBrowserClient } from "@supabase/auth-helpers-nextjs";
import type { SupabaseClient, User } from "@supabase/supabase-js";

import type { DepartmentApiResponse } from "@/lib/api-types";
import {
  DEFAULT_LOCALE,
  type Locale,
  type TranslationKey,
  isLocale,
  translate,
} from "@/lib/i18n";

const DEPARTMENT_STORAGE_KEY = "ncku_current_department_id";
const LOCALE_STORAGE_KEY = "ncku_locale";
const NCKU_EMAIL_DOMAINS = ["@ncku.edu.tw", "@gs.ncku.edu.tw"] as const;
const EMPTY_DEPARTMENTS: DepartmentApiResponse[] = [];

export const USER_ROLES = {
  GUEST: "GUEST",
  USER: "USER",
  NCKU_VERIFIED: "NCKU_VERIFIED",
  ADMIN: "ADMIN",
} as const;

export type UserRole = (typeof USER_ROLES)[keyof typeof USER_ROLES];

export type Department = {
  id: string;
  code: string;
  name: string;
  nameEn: string | null;
  college: string | null;
};

type AppContextValue = {
  currentDepartment: Department;
  setCurrentDepartment: Dispatch<SetStateAction<Department>>;
  departments: Department[];
  isDepartmentsLoading: boolean;
  departmentError: string | null;
  userRole: UserRole;
  user: User | null;
  isAuthLoading: boolean;
  supabase: SupabaseClient | null;
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (
    key: TranslationKey,
    values?: Record<string, string | number>,
  ) => string;
};

const defaultDepartment: Department = {
  id: process.env.NEXT_PUBLIC_DEFAULT_DEPARTMENT_ID ?? "",
  code: "DPS",
  name: "光電科學與工程學系",
  nameEn: "Department of Photonics",
  college: "理學院",
};

const AppContext = createContext<AppContextValue | undefined>(undefined);

function getUserRole(user: User | null): UserRole {
  if (!user) {
    return USER_ROLES.GUEST;
  }

  if (user.app_metadata?.is_admin === true) {
    return USER_ROLES.ADMIN;
  }

  const email = user.email?.trim().toLowerCase() ?? "";
  if (NCKU_EMAIL_DOMAINS.some((domain) => email.endsWith(domain))) {
    return USER_ROLES.NCKU_VERIFIED;
  }
  return USER_ROLES.USER;
}

function mapDepartment(department: DepartmentApiResponse): Department {
  return {
    id: department.id,
    code: department.code,
    name: department.name_zh,
    nameEn: department.name_en,
    college: department.college,
  };
}

export function AppProvider({
  children,
  initialDepartments = EMPTY_DEPARTMENTS,
}: {
  children: ReactNode;
  initialDepartments?: DepartmentApiResponse[];
}) {
  const seededDepartments = useMemo(
    () =>
      initialDepartments
        .filter((department) => department.is_active)
        .map(mapDepartment),
    [initialDepartments],
  );
  const seededCurrentDepartment =
    seededDepartments.find(
      (department) =>
        department.id === process.env.NEXT_PUBLIC_DEFAULT_DEPARTMENT_ID,
    ) ??
    seededDepartments.find((department) => department.code === "DPS") ??
    seededDepartments[0] ??
    defaultDepartment;

  const [currentDepartment, setCurrentDepartmentState] =
    useState<Department>(seededCurrentDepartment);
  const [departments, setDepartments] = useState<Department[]>(
    seededDepartments.length > 0
      ? seededDepartments
      : [defaultDepartment],
  );
  const [isDepartmentsLoading, setIsDepartmentsLoading] = useState(
    seededDepartments.length === 0,
  );
  const [departmentError, setDepartmentError] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [isAuthLoading, setIsAuthLoading] = useState(true);
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);

  const supabase = useMemo<SupabaseClient | null>(() => {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    return url && anonKey ? createBrowserClient(url, anonKey) : null;
  }, []);

  const setCurrentDepartment = useCallback<
    Dispatch<SetStateAction<Department>>
  >((value) => {
    setCurrentDepartmentState((previous) => {
      const next = typeof value === "function" ? value(previous) : value;
      window.localStorage.setItem(DEPARTMENT_STORAGE_KEY, next.id);
      return next;
    });
  }, []);

  const setLocale = useCallback((nextLocale: Locale) => {
    setLocaleState(nextLocale);
    window.localStorage.setItem(LOCALE_STORAGE_KEY, nextLocale);
    document.documentElement.lang =
      nextLocale === "zh-TW" ? "zh-Hant" : "en";
  }, []);

  useEffect(() => {
    const storedLocale = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    const browserLocale = navigator.language?.startsWith("en")
      ? "en-US"
      : DEFAULT_LOCALE;
    const nextLocale = isLocale(storedLocale)
      ? storedLocale
      : browserLocale;

    setLocaleState(nextLocale);
    document.documentElement.lang =
      nextLocale === "zh-TW" ? "zh-Hant" : "en";
  }, []);

  useEffect(() => {
    if (seededDepartments.length > 0) {
      setDepartments(seededDepartments);
      setCurrentDepartmentState((previous) => {
        const storedId = window.localStorage.getItem(
          DEPARTMENT_STORAGE_KEY,
        );
        return (
          seededDepartments.find(
            (department) => department.id === storedId,
          ) ??
          seededDepartments.find(
            (department) => department.id === previous.id,
          ) ??
          seededCurrentDepartment
        );
      });
      setIsDepartmentsLoading(false);
      return;
    }

    const controller = new AbortController();

    async function loadDepartments() {
      setIsDepartmentsLoading(true);
      setDepartmentError(null);

      try {
        const response = await fetch("/api/departments", {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Departments API failed with ${response.status}`);
        }

        const data = (await response.json()) as DepartmentApiResponse[];
        const activeDepartments = data
          .filter((department) => department.is_active)
          .map(mapDepartment);
        if (activeDepartments.length === 0) {
          throw new Error("No active departments returned");
        }

        setDepartments(activeDepartments);
        setCurrentDepartmentState((previous) => {
          const storedId = window.localStorage.getItem(
            DEPARTMENT_STORAGE_KEY,
          );
          return (
            activeDepartments.find(
              (department) => department.id === storedId,
            ) ??
            activeDepartments.find(
              (department) => department.id === previous.id,
            ) ??
            activeDepartments.find(
              (department) => department.code === previous.code,
            ) ??
            activeDepartments[0]
          );
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setDepartmentError(translate(locale, "department.selector.error"));
      } finally {
        if (!controller.signal.aborted) {
          setIsDepartmentsLoading(false);
        }
      }
    }

    void loadDepartments();
    return () => controller.abort();
  }, [locale, seededCurrentDepartment, seededDepartments]);

  useEffect(() => {
    if (!supabase) {
      setUser(null);
      setIsAuthLoading(false);
      return;
    }

    let isMounted = true;
    void supabase.auth
      .getUser()
      .then(({ data }) => {
        if (isMounted) {
          setUser(data.user);
        }
      })
      .catch(() => {
        if (isMounted) {
          setUser(null);
        }
      })
      .finally(() => {
        if (isMounted) {
          setIsAuthLoading(false);
        }
      });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(session?.user ?? null);
      setIsAuthLoading(false);
    });

    return () => {
      isMounted = false;
      subscription.unsubscribe();
    };
  }, [supabase]);

  const value = useMemo<AppContextValue>(
    () => ({
      currentDepartment,
      setCurrentDepartment,
      departments,
      isDepartmentsLoading,
      departmentError,
      userRole: getUserRole(user),
      user,
      isAuthLoading,
      supabase,
      locale,
      setLocale,
      t: (key, values) => translate(locale, key, values),
    }),
    [
      currentDepartment,
      setCurrentDepartment,
      departments,
      isDepartmentsLoading,
      departmentError,
      user,
      isAuthLoading,
      supabase,
      locale,
      setLocale,
    ],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppContext(): AppContextValue {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error("useAppContext must be used inside AppProvider.");
  }
  return context;
}
