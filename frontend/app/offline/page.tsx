import Link from "next/link";
import { ArrowLeft, WifiOff } from "lucide-react";


export default function OfflinePage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-mist px-4 py-12">
      <section className="w-full max-w-lg rounded-lg border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <span className="flex h-11 w-11 items-center justify-center rounded-lg bg-amber-100 text-amber-700">
          <WifiOff className="h-5 w-5" aria-hidden="true" />
        </span>
        <h1 className="mt-5 text-2xl font-bold text-ink">目前無法連線</h1>
        <p className="mt-3 text-sm leading-6 text-slate-600">
          這個頁面尚未儲存在裝置中。已瀏覽過的課程、評價與公開資料仍可從快取開啟。
        </p>
        <Link
          href="/"
          className="mt-6 inline-flex h-10 items-center gap-2 rounded-md bg-campus px-4 text-sm font-semibold text-white transition hover:bg-campus/90"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          返回首頁
        </Link>
      </section>
    </main>
  );
}
