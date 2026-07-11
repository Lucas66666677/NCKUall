import { expect, type Locator, type Page } from "@playwright/test";

export class AiAssistant {
  readonly dialog: Locator;
  readonly input: Locator;
  readonly submitButton: Locator;

  constructor(page: Page) {
    this.dialog = page.getByRole("dialog", {
      name: "AI 資訊小幫手",
      exact: true,
    });
    this.input = this.dialog.getByLabel("AI 問題", { exact: true });
    this.submitButton = this.dialog.getByRole("button", {
      name: "送出問題",
    });
  }

  async expectOpenWithPrompt(prompt: string) {
    await expect(this.dialog).toBeVisible();
    await expect(this.dialog).toHaveClass(/translate-x-0/);
    await expect(this.input).toHaveValue(prompt);
    await expect(this.submitButton).toBeEnabled();
  }
}
