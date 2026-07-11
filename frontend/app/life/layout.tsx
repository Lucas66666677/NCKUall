import type { Metadata } from "next";
import type { ReactNode } from "react";


export const metadata: Metadata = {
  title: "成大生活助手與匿名評價",
  description: "瀏覽成大周邊租屋避雷、美食推薦與高蛋白備餐情報。",
  alternates: { canonical: "/life" },
  openGraph: {
    title: "成大生活助手 - 租屋、美食與備餐情報 | NCKUall",
    description: "來自成大學生的匿名生活經驗，通過成大信箱權限控管。",
    url: "/life",
    images: ["/og/nckuall-social.png"],
  },
};

export default function LifeLayout({ children }: { children: ReactNode }) {
  return children;
}
