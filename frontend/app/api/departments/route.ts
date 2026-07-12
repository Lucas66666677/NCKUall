import { NextResponse } from "next/server";

import { getServerApiBaseUrl } from "@/lib/server-runtime-config";

export const runtime = "edge";
export const preferredRegion = "auto";

const DAY_IN_SECONDS = 86_400;
const WEEK_IN_SECONDS = 604_800;

function getApiBaseUrl() {
  return getServerApiBaseUrl();
}

export async function GET() {
  try {
    const response = await fetch(`${getApiBaseUrl()}/api/departments`, {
      headers: {
        Accept: "application/json",
      },
      next: {
        revalidate: DAY_IN_SECONDS,
        tags: ["departments"],
      },
    });

    if (!response.ok) {
      throw new Error(`Departments API failed with ${response.status}`);
    }

    return NextResponse.json(await response.json(), {
      headers: {
        "Cache-Control": `public, s-maxage=${DAY_IN_SECONDS}, stale-while-revalidate=${WEEK_IN_SECONDS}`,
      },
    });
  } catch {
    return NextResponse.json(
      { detail: "目前無法取得科系資料" },
      { status: 502 },
    );
  }
}
