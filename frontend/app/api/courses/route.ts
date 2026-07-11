import { NextRequest, NextResponse } from "next/server";

import { getCourses } from "@/lib/server-data";

export async function GET(request: NextRequest) {
  const departmentId = request.nextUrl.searchParams.get("department_id");
  if (!departmentId) {
    return NextResponse.json(
      { detail: "department_id is required" },
      { status: 400 },
    );
  }

  try {
    return NextResponse.json(await getCourses(departmentId));
  } catch {
    return NextResponse.json(
      { detail: "目前無法取得課程資料" },
      { status: 502 },
    );
  }
}
