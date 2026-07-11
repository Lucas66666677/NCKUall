"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { GradeDistribution } from "@/lib/api-types";

type ChartDatum = {
  grade: string;
  value: number;
};

const orderedGrades = [
  "A+",
  "A",
  "A-",
  "B+",
  "B",
  "B-",
  "C+",
  "C",
  "C-",
  "D",
  "F",
  "不及格",
];

export function CourseGradeChart({
  distributions,
}: {
  distributions: GradeDistribution[];
}) {
  const chartData = buildGradeChartData(distributions);

  if (chartData.length === 0) {
    return (
      <div className="flex h-56 items-center justify-center rounded-md border border-dashed border-slate-300 bg-slate-50 text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-950/60 dark:text-slate-400">
        尚無可視覺化的成績分佈。
      </div>
    );
  }

  return (
    <div className="h-72 w-full sm:h-80">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={chartData}
          margin={{ top: 12, right: 12, bottom: 4, left: -18 }}
        >
          <CartesianGrid
            stroke="var(--chart-grid)"
            strokeDasharray="3 3"
            vertical={false}
          />
          <XAxis
            dataKey="grade"
            tick={{ fill: "var(--chart-text)" }}
            tickLine={false}
            axisLine={false}
            fontSize={12}
          />
          <YAxis
            tick={{ fill: "var(--chart-text)" }}
            tickLine={false}
            axisLine={false}
            fontSize={12}
            tickFormatter={(value) => `${value}%`}
          />
          <Tooltip
            cursor={{ fill: "var(--chart-cursor)" }}
            contentStyle={{
              backgroundColor: "var(--chart-tooltip-bg)",
              borderColor: "var(--chart-tooltip-border)",
              color: "var(--chart-text)",
              borderRadius: "6px",
            }}
            labelStyle={{ color: "var(--chart-text)" }}
            formatter={(value) => [
              `${Number(value).toFixed(1)}%`,
              "比例",
            ]}
          />
          <Bar
            dataKey="value"
            fill="var(--chart-primary)"
            radius={[5, 5, 0, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function buildGradeChartData(
  distributions: GradeDistribution[],
): ChartDatum[] {
  if (distributions.length === 0) {
    return [];
  }

  const countTotals = new Map<string, number>();
  const ratioTotals = new Map<string, { total: number; count: number }>();

  for (const distribution of distributions) {
    for (const [rawGrade, rawValue] of Object.entries(
      distribution.grade_buckets ?? {},
    )) {
      const value = Number(rawValue);
      if (!Number.isFinite(value)) {
        continue;
      }

      const grade = normalizeGradeLabel(rawGrade);
      if (rawGrade.toLowerCase().includes("ratio")) {
        const current = ratioTotals.get(grade) ?? { total: 0, count: 0 };
        ratioTotals.set(grade, {
          total: current.total + value * 100,
          count: current.count + 1,
        });
      } else {
        countTotals.set(grade, (countTotals.get(grade) ?? 0) + value);
      }
    }
  }

  const countSum = Array.from(countTotals.values()).reduce(
    (sum, value) => sum + value,
    0,
  );
  if (countSum > 0) {
    return sortChartData(
      Array.from(countTotals.entries()).map(([grade, value]) => ({
        grade,
        value: Number(((value / countSum) * 100).toFixed(1)),
      })),
    );
  }

  if (ratioTotals.size > 0) {
    return sortChartData(
      Array.from(ratioTotals.entries()).map(([grade, value]) => ({
        grade,
        value: Number((value.total / value.count).toFixed(1)),
      })),
    );
  }

  return [];
}

function sortChartData(data: ChartDatum[]) {
  return data.sort((a, b) => {
    const aIndex = orderedGrades.indexOf(a.grade);
    const bIndex = orderedGrades.indexOf(b.grade);
    return (aIndex === -1 ? 999 : aIndex) - (bIndex === -1 ? 999 : bIndex);
  });
}

function normalizeGradeLabel(label: string) {
  const normalized = label.trim();
  if (normalized === "A+_ratio") return "A+";
  if (normalized === "fail_ratio" || normalized === "F_ratio") {
    return "不及格";
  }
  if (normalized.toLowerCase() === "f") return "不及格";
  return normalized.replace("_ratio", "");
}
