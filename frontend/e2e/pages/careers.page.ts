import { expect, type Locator, type Page } from "@playwright/test";

export class CareersPage {
  readonly page: Page;
  readonly heading: Locator;
  readonly departmentSelector: Locator;

  constructor(page: Page) {
    this.page = page;
    this.heading = page.getByRole("heading", {
      name: "系所資源與升學地圖",
    });
    this.departmentSelector = page.getByLabel("選擇科系", {
      exact: true,
    });
  }

  async waitUntilReady() {
    await expect(this.page).toHaveURL(/\/careers$/);
    await expect(this.heading).toBeVisible();
    await expect(this.departmentSelector).toBeEnabled();
  }

  async selectDepartment(departmentName: string) {
    await this.departmentSelector.selectOption({ label: departmentName });
    await expect(
      this.departmentSelector.locator("option:checked"),
    ).toHaveText(departmentName);
  }

  professorCard(professorName: string) {
    return this.page.locator("article").filter({
      has: this.page.getByRole("heading", {
        name: professorName,
        exact: true,
      }),
    });
  }

  async askAiAboutProfessor(professorName: string) {
    const card = this.professorCard(professorName);
    await expect(card).toBeVisible();
    await card
      .getByRole("button", {
        name: "詢問 AI 實驗室評價",
        exact: true,
      })
      .click();
  }
}
