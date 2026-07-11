import type { Metadata } from "next";
import type { ReactNode } from "react";


export const metadata: Metadata = {
  title: "成大校園近期活動",
  description: "掌握成大官方活動、社團聚會、舞會、講座與單車節時程。",
  alternates: { canonical: "/events" },
  openGraph: {
    title: "成大校園近期活動 | NCKUall",
    description: "單車節、舞會、社團與官方活動，一站掌握時間與地點。",
    url: "/events",
    images: ["/og/nckuall-social.png"],
  },
};

export default function EventsLayout({ children }: { children: ReactNode }) {
  return children;
}
