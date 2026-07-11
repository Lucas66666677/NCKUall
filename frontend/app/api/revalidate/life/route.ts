import { timingSafeEqual } from "node:crypto";

import { revalidatePath, revalidateTag } from "next/cache";
import { NextRequest, NextResponse } from "next/server";

import { CACHE_TAGS } from "@/lib/server-data";

export const runtime = "nodejs";

function secretsMatch(provided: string, expected: string) {
  const providedBuffer = Buffer.from(provided);
  const expectedBuffer = Buffer.from(expected);
  return (
    providedBuffer.length === expectedBuffer.length &&
    timingSafeEqual(providedBuffer, expectedBuffer)
  );
}

export async function POST(request: NextRequest) {
  const expectedSecret = process.env.REVALIDATION_SECRET;
  if (!expectedSecret) {
    return NextResponse.json(
      { detail: "revalidation is not configured" },
      { status: 503 },
    );
  }

  const authorization = request.headers.get("authorization") ?? "";
  const providedSecret = authorization.startsWith("Bearer ")
    ? authorization.slice(7)
    : "";
  if (!secretsMatch(providedSecret, expectedSecret)) {
    return NextResponse.json(
      { detail: "invalid revalidation credentials" },
      { status: 401 },
    );
  }

  const payload = (await request.json().catch(() => null)) as {
    event?: string;
    review_id?: string;
  } | null;
  if (payload?.event !== "life.review.created") {
    return NextResponse.json(
      { detail: "unsupported revalidation event" },
      { status: 400 },
    );
  }

  revalidateTag(CACHE_TAGS.lifeReviews);
  revalidatePath("/life");

  return NextResponse.json(
    {
      revalidated: true,
      path: "/life",
      tag: CACHE_TAGS.lifeReviews,
      review_id: payload.review_id ?? null,
      revalidated_at: new Date().toISOString(),
    },
    {
      headers: {
        "Cache-Control": "no-store",
      },
    },
  );
}
