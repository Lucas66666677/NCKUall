import { expect, type Locator, type Page } from "@playwright/test";

export class HomePage {
  readonly page: Page;
  readonly heading: Locator;
  readonly desktopNavigation: Locator;
  readonly mobileMenuButton: Locator;
  readonly mobileNavigation: Locator;

  constructor(page: Page) {
    this.page = page;
    this.heading = page.getByRole("heading", {
      name: "選課規劃",
      exact: true,
    });
    this.desktopNavigation = page.getByRole("navigation", {
      name: "主導覽",
    });
    this.mobileMenuButton = page.getByRole("button", {
      name: "開啟導覽",
    });
    this.mobileNavigation = page.getByRole("navigation", {
      name: "行動版主導覽",
    });
  }

  async goto() {
    await this.page.goto("/");
    await expect(this.heading).toBeVisible();
    await expect(
      this.page.getByRole("combobox", { name: "目前科系" }),
    ).toBeEnabled();
  }

  async openCareers() {
    const desktopLink = this.desktopNavigation.locator(
      'a[href="/careers"]',
    );

    if (await desktopLink.isVisible()) {
      await Promise.all([
        this.page.waitForURL(/\/careers$/),
        desktopLink.click(),
      ]);
      return;
    }

    await this.mobileMenuButton.click();
    await expect(this.mobileNavigation).toBeVisible();
    await Promise.all([
      this.page.waitForURL(/\/careers$/),
      this.mobileNavigation.locator('a[href="/careers"]').click(),
    ]);
  }
}
