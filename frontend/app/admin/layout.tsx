import type { Metadata } from "next";
import type { ReactNode } from "react";


export const metadata: Metadata = {
  title: "管理後台",
  description: "NCKUall 內容審核與營運管理後台。",
  robots: {
    index: false,
    follow: false,
    noarchive: true,
  },
};

export default function AdminLayout({ children }: { children: ReactNode }) {
  return children;
}
