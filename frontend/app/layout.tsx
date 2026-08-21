import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import { AppProvider } from "@/components/AppContext";
import { GlobalAIAssistant } from "@/components/GlobalAIAssistant";
import { OfflineToast } from "@/components/OfflineToast";
import { RealtimeNotifications } from "@/components/RealtimeNotifications";
import { SiteChrome } from "@/components/SiteChrome";
import { ThemeProvider } from "@/components/ThemeProvider";
import { getDepartments } from "@/lib/server-data";
import "./globals.css";

const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

export function generateMetadata(): Metadata {
  const title = "成大資源整合平台 - 擺脫校園資訊落差";
  const description =
    "整合成大選課成績分佈、實驗室與升學資訊、校園活動及生活評價，以真實資料為主，AI 問答為輔。";

  return {
    metadataBase: new URL(siteUrl),
    title: {
      default: title,
      template: "%s | NCKUall",
    },
    description,
    applicationName: "NCKUall by Lucirel",
    manifest: "/manifest.json",
    keywords: [
      "成功大學",
      "成大選課",
      "成績分佈",
      "實驗室評價",
      "成大活動",
      "成大租屋",
    ],
    alternates: {
      canonical: "/",
    },
    openGraph: {
      type: "website",
      locale: "zh_TW",
      url: "/",
      siteName: "NCKUall",
      title,
      description,
      images: [
        {
          url: "/og/nckuall-social.png",
          width: 1200,
          height: 630,
          alt: "NCKUall 成大資源整合平台",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: ["/og/nckuall-social.png"],
    },
    robots: {
      index: true,
      follow: true,
    },
    appleWebApp: {
      capable: true,
      statusBarStyle: "default",
      title: "NCKUall",
    },
    other: {
      "mobile-web-app-capable": "yes",
    },
    formatDetection: {
      telephone: false,
    },
    icons: {
      icon: [
        {
          url: "/icons/icon-192x192.png",
          sizes: "192x192",
          type: "image/png",
        },
        {
          url: "/icons/icon-512x512.png",
          sizes: "512x512",
          type: "image/png",
        },
      ],
      apple: [
        {
          url: "/icons/apple-touch-icon.png",
          sizes: "180x180",
          type: "image/png",
        },
      ],
    },
  };
}

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#1d4ed8" },
    { media: "(prefers-color-scheme: dark)", color: "#081411" },
  ],
  colorScheme: "light dark",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const initialDepartments = await getDepartments();
  const nonce = (await headers()).get("x-nonce") ?? undefined;

  return (
    <html lang="zh-Hant" suppressHydrationWarning>
      <body>
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          enableColorScheme
          disableTransitionOnChange
          nonce={nonce}
          storageKey="nckuall-theme"
        >
          <AppProvider initialDepartments={initialDepartments}>
            <OfflineToast />
            <RealtimeNotifications />
            <SiteChrome />
            <GlobalAIAssistant />
            {children}
          </AppProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
