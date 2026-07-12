"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";

import { AIAssistantSidebar } from "@/components/AIAssistant";
import { useAppContext } from "@/components/AppContext";

const ROUTES_WITH_LOCAL_ASSISTANT = new Set(["/", "/careers"]);

export function GlobalAIAssistant() {
  const pathname = usePathname();
  const { currentDepartment } = useAppContext();
  const [isOpen, setIsOpen] = useState(false);

  if (
    ROUTES_WITH_LOCAL_ASSISTANT.has(pathname) ||
    pathname.startsWith("/admin")
  ) {
    return null;
  }

  return (
    <AIAssistantSidebar
      open={isOpen}
      departmentFilter={currentDepartment.name}
      onOpenChange={setIsOpen}
    />
  );
}
