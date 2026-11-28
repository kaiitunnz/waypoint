"use client";

import type { ReactNode } from "react";

import { useKeyboardInsetVar } from "@waypoint/web-core/hooks/useKeyboardInsetVar";

import { ToastProvider } from "@/components/ui/Toast";
import { useToolColoursAttribute } from "@/lib/tool-colours";

import { AuthGate } from "./AuthGate";
import { FileDropGuard } from "./FileDropGuard";

export function Providers({ children }: { children: ReactNode }) {
  useToolColoursAttribute();
  useKeyboardInsetVar();
  return (
    <ToastProvider>
      <FileDropGuard />
      <AuthGate>{children}</AuthGate>
    </ToastProvider>
  );
}
