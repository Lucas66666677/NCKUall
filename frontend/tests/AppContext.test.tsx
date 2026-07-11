import { useEffect } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  AppProvider,
  type Department,
  useAppContext,
} from "@/components/AppContext";
import type { DepartmentApiResponse } from "@/lib/api-types";

const supabaseMocks = vi.hoisted(() => ({
  getUser: vi.fn(),
  getSession: vi.fn(),
  onAuthStateChange: vi.fn(),
  signInWithOAuth: vi.fn(),
  signOut: vi.fn(),
}));

vi.mock("@supabase/auth-helpers-nextjs", () => ({
  createBrowserClient: vi.fn(() => ({
    auth: {
      getUser: supabaseMocks.getUser,
      getSession: supabaseMocks.getSession,
      onAuthStateChange: supabaseMocks.onAuthStateChange,
      signInWithOAuth: supabaseMocks.signInWithOAuth,
      signOut: supabaseMocks.signOut,
    },
  })),
}));

const departments: DepartmentApiResponse[] = [
  {
    id: "dept-photonics",
    code: "DPS",
    name_zh: "光電科學與工程學系",
    name_en: "Department of Photonics",
    college: "理學院",
    is_active: true,
  },
  {
    id: "dept-ee",
    code: "EE",
    name_zh: "電機工程學系",
    name_en: "Department of Electrical Engineering",
    college: "電機資訊學院",
    is_active: true,
  },
];

function DepartmentDrivenProbe() {
  const { currentDepartment, departments, setCurrentDepartment } =
    useAppContext();

  useEffect(() => {
    if (!currentDepartment.id) {
      return;
    }

    void fetch(`/api/courses?department_id=${currentDepartment.id}`);
  }, [currentDepartment.id]);

  function switchToElectricalEngineering() {
    const nextDepartment = departments.find(
      (department: Department) => department.code === "EE",
    );
    if (nextDepartment) {
      setCurrentDepartment(nextDepartment);
    }
  }

  return (
    <div>
      <p data-testid="current-department">{currentDepartment.name}</p>
      <button type="button" onClick={switchToElectricalEngineering}>
        切換到電機
      </button>
    </div>
  );
}

describe("AppProvider department state", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co";
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon-key";
    process.env.NEXT_PUBLIC_DEFAULT_DEPARTMENT_ID = "dept-photonics";

    supabaseMocks.getUser.mockResolvedValue({ data: { user: null } });
    supabaseMocks.getSession.mockResolvedValue({ data: { session: null } });
    supabaseMocks.onAuthStateChange.mockReturnValue({
      data: {
        subscription: {
          unsubscribe: vi.fn(),
        },
      },
    });

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [],
    }) as unknown as typeof fetch;
  });

  it("propagates currentDepartment changes and triggers department-scoped API refetches", async () => {
    render(
      <AppProvider initialDepartments={departments}>
        <DepartmentDrivenProbe />
      </AppProvider>,
    );

    expect(screen.getByTestId("current-department")).toHaveTextContent(
      "光電科學與工程學系",
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/courses?department_id=dept-photonics",
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "切換到電機" }));

    expect(screen.getByTestId("current-department")).toHaveTextContent(
      "電機工程學系",
    );
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/courses?department_id=dept-ee",
      );
    });
  });
});
