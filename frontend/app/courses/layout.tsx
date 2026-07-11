import type { Metadata } from "next";
import type { ReactNode } from "react";


export const metadata: Metadata = {
  title: "選課規劃與歷年成績分佈",
  description:
    "查詢成大課程、授課教師、學分、課綱與歷年成績分佈，快速掌握課程難度。",
  alternates: { canonical: "/courses" },
  openGraph: {
    title: "成大選課規劃 - 課程難度與歷年成績 | NCKUall",
    description: "用真實成績分佈與課程資料，看懂每一門課的硬度。",
    url: "/courses",
    images: ["/og/nckuall-social.png"],
  },
};

export default function CoursesLayout({ children }: { children: ReactNode }) {
  return children;
}
