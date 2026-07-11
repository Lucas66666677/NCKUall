import { expect, test } from "@playwright/test";

import { AiAssistant } from "./pages/ai-assistant.component";
import { CareersPage } from "./pages/careers.page";
import {
  installCoreApiMocks,
  LAB_PROMPT,
  PHOTONICS_DEPARTMENT_ID,
  PROFESSOR_NAME,
} from "./fixtures/api-mocks";
import { HomePage } from "./pages/home.page";
import { LifePage } from "./pages/life.page";

test.describe("NCKUall 核心使用者流程", () => {
  test("場景 A：遊客切換科系、瀏覽教授並預填 AI 實驗室提問", async ({
    page,
  }) => {
    const api = await installCoreApiMocks(page);
    const home = new HomePage(page);
    const careers = new CareersPage(page);
    const assistant = new AiAssistant(page);

    await test.step("從首頁導覽至職涯規劃", async () => {
      await home.goto();
      await home.openCareers();
      await careers.waitUntilReady();
    });

    await test.step("切換到光電系並驗證教授卡片", async () => {
      await careers.selectDepartment("光電科學與工程學系");
      await expect
        .poll(() => api.careerDepartmentIds.at(-1))
        .toBe(PHOTONICS_DEPARTMENT_ID);
      await expect(careers.professorCard(PROFESSOR_NAME)).toBeVisible();
      await expect(
        careers.professorCard(PROFESSOR_NAME).getByText("矽光子", {
          exact: true,
        }),
      ).toBeVisible();
    });

    await test.step("開啟 AI 側欄並驗證預設 Prompt", async () => {
      await careers.askAiAboutProfessor(PROFESSOR_NAME);
      await assistant.expectOpenWithPrompt(LAB_PROMPT);
      expect(api.chatPayloads).toHaveLength(0);
    });
  });

  test("場景 B：遊客無法發布生活評論", async ({ page }) => {
    const api = await installCoreApiMocks(page);
    const life = new LifePage(page);

    await life.goto();
    await life.openReviewComposer();

    await test.step("顯示成大信箱限制並禁用全部輸入", async () => {
      await life.expectGuestIsBlocked();
    });

    await test.step("即使繞過 disabled UI 觸發 submit 也不送出 API", async () => {
      await life.attemptSubmitBypassingDisabledButton();
      await expect(life.reviewDialog).toBeVisible();
      await expect.poll(() => api.lifeReviewPostCount).toBe(0);
    });
  });
});
