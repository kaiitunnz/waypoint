"use client";

import { useState } from "react";

import { setSessionFocus } from "@waypoint/web-core/api";
import type { SessionRecord } from "@waypoint/web-core/types";

import { useFailureToast } from "@/lib/useFailureToast";

interface Options {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  onSession: (session: SessionRecord) => void;
}

export function useSessionFocus({ host, token, sessionId, session, onSession }: Options) {
  const reportFailure = useFailureToast();
  const [busy, setBusy] = useState(false);

  async function set(enabled: boolean) {
    setBusy(true);
    try {
      onSession(await setSessionFocus(host, token, sessionId, enabled));
    } catch (error) {
      reportFailure(error, enabled ? "failed to turn on Focus" : "failed to turn off Focus");
    } finally {
      setBusy(false);
    }
  }

  return { on: Boolean(session?.focus), busy, set };
}

export type SessionFocus = ReturnType<typeof useSessionFocus>;
