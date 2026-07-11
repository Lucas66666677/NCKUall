import type { Metadata } from "next";
import type { ReactNode } from "react";


export const metadata: Metadata = {
  title: "職涯規劃與實驗室資訊",
  description:
    "依成大科系整理教授實驗室、海外交換、雙聯學位、預研與推甄資訊。",
  alternates: { canonical: "/careers" },
  openGraph: {
    title: "成大職涯規劃與實驗室資訊 | NCKUall",
    description: "依科系整理實驗室、交換、雙聯、預研及推甄資料。",
    url: "/careers",
    images: ["/og/nckuall-social.png"],
  },
};

export default function CareersLayout({ children }: { children: ReactNode }) {
  return children;
}
