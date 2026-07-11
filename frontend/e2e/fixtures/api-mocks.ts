import type { Page, Route } from "@playwright/test";

export const PHOTONICS_DEPARTMENT_ID =
  "11111111-1111-4111-8111-111111111111";
export const ELECTRICAL_ENGINEERING_DEPARTMENT_ID =
  "22222222-2222-4222-8222-222222222222";

export const PROFESSOR_NAME = "王光明";
export const LAB_PROMPT =
  `請告訴我關於 ${PROFESSOR_NAME} 實驗室的評價與專題規定`;

const departments = [
  {
    id: PHOTONICS_DEPARTMENT_ID,
    code: "DPS",
    name_zh: "光電科學與工程學系",
    name_en: "Department of Photonics",
    college: "理學院",
    is_active: true,
  },
  {
    id: ELECTRICAL_ENGINEERING_DEPARTMENT_ID,
    code: "EE",
    name_zh: "電機工程學系",
    name_en: "Department of Electrical Engineering",
    college: "電機資訊學院",
    is_active: true,
  },
];

const labResources = [
  {
    id: "33333333-3333-4333-8333-333333333333",
    department_id: PHOTONICS_DEPARTMENT_ID,
    resource_type: "lab_review",
    title: "先進光電元件實驗室",
    organization_name: "光電科學與工程學系",
    professor_name: PROFESSOR_NAME,
    location: "理學大樓",
    summary: "研究矽光子、積體光學與光通訊元件。",
    requirements: "需修習光電子學並參與每週專題討論。",
    application_timeline: "每學期開學前公告",
    official_url: "https://example.edu.tw/labs/photonics",
    source_url: "https://example.edu.tw/faculty/wang",
    tags: ["矽光子", "積體光學", "光通訊"],
    created_at: "2026-07-01T00:00:00+08:00",
    updated_at: "2026-07-01T00:00:00+08:00",
  },
];

export type MockApiState = {
  careerDepartmentIds: string[];
  chatPayloads: Array<Record<string, unknown>>;
  lifeReviewPostCount: number;
};

async function fulfillJson(route: Route, json: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json; charset=utf-8",
    body: JSON.stringify(json),
  });
}

export async function installCoreApiMocks(
  page: Page,
): Promise<MockApiState> {
  const state: MockApiState = {
    careerDepartmentIds: [],
    chatPayloads: [],
    lifeReviewPostCount: 0,
  };

  await page.addInitScript(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  await page.route("**/api/departments", async (route) => {
    await fulfillJson(route, departments);
  });

  await page.route(/\/api\/careers(?:\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    state.careerDepartmentIds.push(url.searchParams.get("department_id") ?? "");
    await fulfillJson(route, labResources);
  });

  await page.route(/\/api\/life\/reviews(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      state.lifeReviewPostCount += 1;
      await fulfillJson(route, { id: "unexpected-post" }, 201);
      return;
    }
    await fulfillJson(route, []);
  });

  await page.route("**/api/chat", async (route) => {
    state.chatPayloads.push(
      (await route.request().postDataJSON()) as Record<string, unknown>,
    );
    await fulfillJson(route, {
      answer: "這是測試用回答。",
      citations: [],
      retrieved_count: 0,
    });
  });

  await page.route(/\/api\/analytics\/trending(?:\?.*)?$/, async (route) => {
    await fulfillJson(route, {
      window_hours: 74,
      courses: [],
      labs: [],
      events: [],
    });
  });

  await page.route(
    /\/api\/search\/suggestions(?:\?.*)?$/,
    async (route) => {
      await fulfillJson(route, []);
    },
  );

  return state;
}
