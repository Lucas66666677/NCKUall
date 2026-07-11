import { expect, type Locator, type Page } from "@playwright/test";

export class LifePage {
  readonly page: Page;
  readonly heading: Locator;
  readonly openReviewButton: Locator;
  readonly reviewDialog: Locator;
  readonly warning: Locator;
  readonly reviewType: Locator;
  readonly area: Locator;
  readonly title: Locator;
  readonly content: Locator;
  readonly submitButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.heading = page.getByRole("heading", {
      name: "成大生活情報站",
    });
    this.openReviewButton = page.getByRole("button", {
      name: "分享生活情報",
    });
    this.reviewDialog = page.getByRole("dialog", {
      name: "發布生活評價",
    });
    this.warning = this.reviewDialog.getByText(
      "本平台為維護真實性，僅限綁定成大信箱之帳號發布評價，遊客僅供瀏覽。",
      { exact: true },
    );
    this.reviewType = this.reviewDialog.getByRole("combobox", {
      name: "類型",
      exact: true,
    });
    this.area = this.reviewDialog.getByRole("textbox", {
      name: "區域",
      exact: true,
    });
    this.title = this.reviewDialog.getByRole("textbox", {
      name: "標題",
      exact: true,
    });
    this.content = this.reviewDialog.getByRole("textbox", {
      name: "內容",
      exact: true,
    });
    this.submitButton = this.reviewDialog.getByRole("button", {
      name: "發布評價",
      exact: true,
    });
  }

  async goto() {
    await this.page.goto("/life");
    await expect(this.heading).toBeVisible();
  }

  async openReviewComposer() {
    await this.openReviewButton.click();
    await expect(this.reviewDialog).toBeVisible();
  }

  async expectGuestIsBlocked() {
    await expect(this.warning).toBeVisible();
    await expect(this.warning.locator("..")).toHaveClass(/bg-amber-50/);
    await expect(this.reviewType).toBeDisabled();
    await expect(this.area).toBeDisabled();
    await expect(this.title).toBeDisabled();
    await expect(this.content).toBeDisabled();
    await expect(this.submitButton).toBeDisabled();
  }

  async attemptSubmitBypassingDisabledButton() {
    await this.reviewDialog.locator("form").evaluate((form) => {
      (form as HTMLFormElement).requestSubmit();
    });
  }
}
