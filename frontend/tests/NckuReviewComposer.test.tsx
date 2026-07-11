import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider } from "@/components/AppContext";
import { NckuReviewComposer } from "@/components/NckuReviewComposer";
import type { DepartmentApiResponse } from "@/lib/api-types";

type MockUser = {
  email: string;
  app_metadata?: Record<string, unknown>;
};

const supabaseMocks = vi.hoisted(() => ({
  currentUser: null as MockUser | null,
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

const initialDepartments: DepartmentApiResponse[] = [
  {
    id: "dept-photonics",
    code: "DPS",
    name_zh: "光電科學與工程學系",
    name_en: "Department of Photonics",
    college: "理學院",
    is_active: true,
  },
];

function renderReviewComposer(user: MockUser | null) {
  supabaseMocks.currentUser = user;
  supabaseMocks.getUser.mockResolvedValue({ data: { user } });
  supabaseMocks.getSession.mockResolvedValue({
    data: {
      session: user
        ? {
            access_token: "mock-access-token",
            user,
          }
        : null,
    },
  });
  supabaseMocks.onAuthStateChange.mockReturnValue({
    data: {
      subscription: {
        unsubscribe: vi.fn(),
      },
    },
  });

  return render(
    <AppProvider initialDepartments={initialDepartments}>
      <NckuReviewComposer />
    </AppProvider>,
  );
}

describe("NckuReviewComposer RBAC", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://example.supabase.co";
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY = "anon-key";
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000";

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: "review-1" }),
    }) as unknown as typeof fetch;
  });

  it.each([
    {
      label: "guest",
      user: null,
      warningText: "請登入以發布評價",
    },
    {
      label: "general user",
      user: { email: "student@gmail.com" },
      warningText:
        "僅限成大教職員工生 (@ncku.edu.tw 或 @gs.ncku.edu.tw) 發布評價，以維持資訊真實性",
    },
  ])(
    "disables the review form and shows a warning for $label",
    async ({ user, warningText }) => {
      renderReviewComposer(user);

      expect(await screen.findByText(warningText)).toBeInTheDocument();
      expect(screen.getByLabelText("標題")).toBeDisabled();
      expect(screen.getByLabelText("內容")).toBeDisabled();
      expect(screen.getByRole("button", { name: /發布評價/ })).toBeDisabled();

      await userEvent.click(screen.getByRole("button", { name: /發布評價/ }));

      expect(global.fetch).not.toHaveBeenCalled();
    },
  );

  it("enables verified NCKU users to submit a review payload", async () => {
    renderReviewComposer({ email: "student@gs.ncku.edu.tw" });

    expect(
      await screen.findByText("已通過成大信箱認證，可以發布評價。"),
    ).toBeInTheDocument();

    const titleInput = screen.getByLabelText("標題");
    const contentInput = screen.getByLabelText("內容");
    const areaInput = screen.getByLabelText("區域");
    const locationInput = screen.getByLabelText("地點或標的");
    const submitButton = screen.getByRole("button", { name: /發布評價/ });

    expect(titleInput).toBeEnabled();
    expect(contentInput).toBeEnabled();
    expect(areaInput).toBeEnabled();
    expect(locationInput).toBeEnabled();
    expect(submitButton).toBeDisabled();

    await userEvent.type(areaInput, "勝利校區");
    await userEvent.type(locationInput, "某備餐店");
    await userEvent.type(titleInput, "價格透明且份量穩定");
    await userEvent.type(contentInput, "雞胸肉份量足，尖峰時段需要先預訂。");

    expect(submitButton).toBeEnabled();
    await userEvent.click(submitButton);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "http://127.0.0.1:8000/api/life/reviews",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            Authorization: "Bearer mock-access-token",
            "Content-Type": "application/json",
          }),
          body: expect.any(String),
        }),
      );
    });

    const [, requestInit] = vi.mocked(global.fetch).mock.calls[0];
    const payload = JSON.parse(String(requestInit?.body));
    expect(payload).toMatchObject({
      review_type: "rental_warning",
      title: "價格透明且份量穩定",
      content: "雞胸肉份量足，尖峰時段需要先預訂。",
      location_name: "某備餐店",
      area: "勝利校區",
      author_alias: "student",
      tags: ["租屋", "避雷"],
    });
  });
});
