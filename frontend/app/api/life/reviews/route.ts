import { NextRequest, NextResponse } from "next/server";

import type { LifeReviewType } from "@/lib/api-types";
import { getLifeReviews } from "@/lib/server-data";

const allowedReviewTypes = new Set<LifeReviewType>([
  "rental_warning",
  "rental_recommendation",
  "food_recommendation",
  "protein_meal_prep",
  "other",
]);

export async function GET(request: NextRequest) {
  const requestedType = request.nextUrl.searchParams.get("review_type");
  if (
    requestedType &&
    !allowedReviewTypes.has(requestedType as LifeReviewType)
  ) {
    return NextResponse.json(
      { detail: "invalid review_type" },
      { status: 400 },
    );
  }

  try {
    return NextResponse.json(
      await getLifeReviews(
        (requestedType as LifeReviewType | null) ?? undefined,
      ),
    );
  } catch {
    return NextResponse.json(
      { detail: "目前無法取得生活評價" },
      { status: 502 },
    );
  }
}
