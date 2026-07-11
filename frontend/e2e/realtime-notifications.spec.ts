import { expect, test } from "@playwright/test";
import type { WebSocketRoute } from "@playwright/test";

import {
  installCoreApiMocks,
  PHOTONICS_DEPARTMENT_ID,
} from "./fixtures/api-mocks";


test("receives a department-aware notification and opens its destination", async ({
  page,
}) => {
  let notificationSocket: WebSocketRoute | null = null;
  const socketUrls: string[] = [];

  await page.routeWebSocket(/\/ws\/notifications(?:\?.*)?$/, (socket) => {
    socketUrls.push(socket.url());
    notificationSocket = socket;
  });
  await installCoreApiMocks(page);
  await page.route(/\/api\/events(?:\?.*)?$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: "[]",
    });
  });

  await page.goto("/");
  await expect
    .poll(() =>
      socketUrls.some(
        (url) =>
          new URL(url).searchParams.get("department_id") ===
          PHOTONICS_DEPARTMENT_ID,
      ),
    )
    .toBe(true);

  expect(notificationSocket).not.toBeNull();
  notificationSocket!.send(
    JSON.stringify({
      event: "notification",
      data: {
        id: "notification-bike-festival",
        kind: "event.created",
        topic: "all",
        title: "成大單車節售票開始",
        summary: "本週五中午開放售票，活動地點為光復校區。",
        href: "/events#event-bike-festival",
        resource_id: "bike-festival",
        created_at: "2026-07-05T12:00:00Z",
      },
    }),
  );

  const toast = page.getByTestId("realtime-notification");
  await expect(toast).toBeVisible();
  await expect(toast).toContainText("成大單車節售票開始");
  await expect(toast).toContainText("本週五中午開放售票");

  const action = toast.getByRole("button", { name: "點擊前往" });
  await expect(action).toHaveCount(1);
  await action.click();
  await expect(page).toHaveURL(/\/events#event-bike-festival$/);
});
