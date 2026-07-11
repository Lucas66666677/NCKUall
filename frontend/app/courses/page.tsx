import { CoursesClient } from "@/app/courses/CoursesClient";
import {
  getCourses,
  getDepartments,
  selectDefaultDepartment,
} from "@/lib/server-data";

export const revalidate = 86400;

export default async function CoursesPage() {
  const departments = await getDepartments();
  const defaultDepartment = selectDefaultDepartment(departments);
  const initialCourses = defaultDepartment
    ? await getCourses(defaultDepartment.id)
    : [];

  return (
    <CoursesClient
      initialCourses={initialCourses}
      initialDepartmentId={defaultDepartment?.id ?? ""}
    />
  );
}
