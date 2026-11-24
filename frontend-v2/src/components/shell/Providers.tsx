"use client";

import type { ReactNode } from "react";

import { ToastProvider } from "@/components/ui/Toast";

import { AuthGate } from "./AuthGate";
import { FileDropGuard } from "./FileDropGuard";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ToastProvider>
      <FileDropGuard />
      <AuthGate>{children}</AuthGate>
    </ToastProvider>
  );
}
