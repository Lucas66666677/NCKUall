"use client";

import { useEffect, useState } from "react";
import { WifiOff } from "lucide-react";


export function OfflineToast() {
  const [isOffline, setIsOffline] = useState(false);

  useEffect(() => {
    const updateConnectionStatus = () => {
      setIsOffline(!navigator.onLine);
    };

    updateConnectionStatus();
    window.addEventListener("online", updateConnectionStatus);
    window.addEventListener("offline", updateConnectionStatus);

    return () => {
      window.removeEventListener("online", updateConnectionStatus);
      window.removeEventListener("offline", updateConnectionStatus);
    };
  }, []);

  if (!isOffline) {
    return null;
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-3 top-3 z-[100] mx-auto flex min-h-12 max-w-xl items-center gap-3 rounded-lg border border-amber-300 bg-amber-50/95 px-4 py-3 text-sm font-medium text-amber-950 shadow-lg backdrop-blur-md sm:inset-x-6"
    >
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-amber-100 text-amber-700">
        <WifiOff className="h-4 w-4" aria-hidden="true" />
      </span>
      <span>目前處於離線狀態，正為您顯示暫存資料</span>
    </div>
  );
}
