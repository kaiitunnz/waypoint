"use client";

import { useCallback } from "react";

import { isAuthError } from "@waypoint/web-core/api";

import { useToast } from "@/components/ui/Toast";

import { handleAuthFailure } from "./auth";

// Toasts a failed request, or signs out when its token was refused.
export function useFailureToast(): (error: unknown, fallback: string) => void {
  const { toast } = useToast();
  return useCallback(
    (error, fallback) => {
      if (isAuthError(error)) handleAuthFailure();
      else toast({ message: error instanceof Error ? error.message : fallback, tone: "error" });
    },
    [toast],
  );
}
