import { getServerApiBaseUrl } from "@/lib/server-runtime-config";

export type CourseDetail = {
  id: string;
  course_code: string;
  title_zh: string;
  title_en: string | null;
  instructor_name: string | null;
  credits: string | number | null;
  required_for_major: boolean;
  description: string | null;
  syllabus_url: string | null;
  department: {
    name_zh: string;
  } | null;
};

const serverApiBaseUrl = getServerApiBaseUrl();

export async function getCourse(courseId: string): Promise<CourseDetail | null> {
  try {
    const response = await fetch(
      `${serverApiBaseUrl}/api/courses/${encodeURIComponent(courseId)}`,
      {
        next: { revalidate: 300 },
      },
    );
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as CourseDetail;
  } catch {
    return null;
  }
}
