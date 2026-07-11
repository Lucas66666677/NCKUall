import { expect, test } from "@playwright/test";

const webhookPayload = {
  event: "life.review.created",
  review_id: "44444444-4444-4444-8444-444444444444",
};

test.describe("ISR revalidation webhook", () => {
  test("拒絕錯誤密鑰並接受 FastAPI 的合法事件", async ({
    request,
  }) => {
    const unauthorized = await request.post("/api/revalidate/life", {
      headers: {
        Authorization: "Bearer wrong-secret",
      },
      data: webhookPayload,
    });
    expect(unauthorized.status()).toBe(401);

    const authorized = await request.post("/api/revalidate/life", {
      headers: {
        Authorization:
          "Bearer playwright-only-revalidation-secret-32-characters",
      },
      data: webhookPayload,
    });
    expect(authorized.status()).toBe(200);
    await expect(authorized.json()).resolves.toMatchObject({
      revalidated: true,
      path: "/life",
      tag: "life-reviews",
      review_id: webhookPayload.review_id,
    });
  });
});
