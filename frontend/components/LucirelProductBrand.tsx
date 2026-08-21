import Image from "next/image";

import { LUCIREL_WAVE_GATE_ICON } from "@/components/lucirelBrandAsset";

export function LucirelProductBrand({ compact = false }: { compact?: boolean }) {
  return (
    <span className="flex min-w-0 items-center gap-3">
      <span className="grid h-10 w-10 shrink-0 place-items-center overflow-hidden rounded-xl bg-[#faf8f2] shadow-sm ring-1 ring-slate-200/80">
        <Image
          alt="Lucirel Wave Gate"
          src={LUCIREL_WAVE_GATE_ICON}
          width={40}
          height={40}
          unoptimized
          className="h-10 w-10 object-cover"
        />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-base font-bold tracking-tight text-ink dark:text-slate-100">
          NCKUall
        </span>
        {!compact && (
          <span className="block truncate text-[11px] font-medium tracking-wide text-slate-500 dark:text-slate-400">
            成大資源整合 · by Lucirel
          </span>
        )}
      </span>
    </span>
  );
}
