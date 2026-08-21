import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import {
  ArrowLeft,
  BookOpen,
  ExternalLink,
  GraduationCap,
  UserRound,
} from "lucide-react";

import { getCourse } from "@/lib/course-api";


type CoursePageProps = {
  params: Promise<{
    id: string;
  }>;
};

export async function generateMetadata({
  params,
}: CoursePageProps): Promise<Metadata> {
  const { id } = await params;
  const course = await getCourse(id);
  if (!course) {
    return {
      title: "找不到課程",
      robots: { index: false, follow: false },
    };
  }

  const title = `${course.title_zh} - 歷年成績分佈與課綱評價 | NCKUall`;
  const description = `${course.title_zh}（${course.course_code}）歷年成績分佈、授課教師、學分與課綱評價。`;

  return {
    title: { absolute: title },
    description,
    alternates: {
      canonical: `/courses/${course.id}`,
    },
    openGraph: {
      type: "article",
      title,
      description,
      url: `/courses/${course.id}`,
      siteName: "NCKUall",
      images: [
        {
          url: "/og/nckuall-social.png",
          width: 1200,
          height: 630,
          alt: `${course.title_zh} | NCKUall`,
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: ["/og/nckuall-social.png"],
    },
  };
}

export default async function CourseDetailPage({ params }: CoursePageProps) {
  const { id } = await params;
  const course = await getCourse(id);
  if (!course) {
    notFound();
  }

  return (
    <main className="min-h-screen bg-mist pb-16">
      <header className="border-b border-white/60 bg-white/80 backdrop-blur-md">
        <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
          <Link
            href="/courses"
            className="inline-flex items-center gap-2 text-sm font-semibold text-campus hover:text-campus/80"
          >
            <ArrowLeft className="h-4 w-4" />
            返回選課規劃
          </Link>
          <p className="mt-6 text-sm font-semibold text-campus">
            {course.department?.name_zh ?? "成大課程"} · {course.course_code}
          </p>
          <h1 className="mt-2 text-3xl font-bold tracking-normal text-ink sm:text-4xl">
            {course.title_zh}
          </h1>
          {course.title_en && (
            <p className="mt-2 text-base text-slate-500">{course.title_en}</p>
          )}
        </div>
      </header>

      <section className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
        <div className="grid gap-4 sm:grid-cols-3">
          <Fact
            icon={<UserRound className="h-5 w-5" />}
            label="授課教師"
            value={course.instructor_name ?? "待公告"}
          />
          <Fact
            icon={<GraduationCap className="h-5 w-5" />}
            label="學分"
            value={course.credits === null ? "待公告" : `${course.credits} 學分`}
          />
          <Fact
            icon={<BookOpen className="h-5 w-5" />}
            label="課程類型"
            value={course.required_for_major ? "必修" : "選修"}
          />
        </div>

        <div className="mt-8 border-t border-slate-200 pt-6">
          <h2 className="text-xl font-bold tracking-normal text-ink">課程介紹</h2>
          <p className="mt-3 whitespace-pre-line text-sm leading-7 text-slate-600">
            {course.description ?? "目前尚無課程介紹。"}
          </p>
          {course.syllabus_url && (
            <a
              href={course.syllabus_url}
              target="_blank"
              rel="noreferrer"
              className="mt-5 inline-flex h-10 items-center gap-2 rounded-md bg-campus px-4 text-sm font-semibold text-white"
            >
              <ExternalLink className="h-4 w-4" />
              查看官方課綱
            </a>
          )}
        </div>
      </section>
    </main>
  );
}

function Fact({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center gap-2 text-campus">
        {icon}
        <span className="text-xs font-semibold">{label}</span>
      </div>
      <p className="mt-3 text-base font-bold text-ink">{value}</p>
    </div>
  );
}
