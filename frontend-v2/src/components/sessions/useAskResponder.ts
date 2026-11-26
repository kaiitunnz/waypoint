"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { isAuthError } from "@waypoint/web-core/api";
import { isAgentNotReady, NOT_READY_MESSAGE, type RetryOptions } from "@waypoint/web-core/pendingAskActions";

import { handleAuthFailure } from "@/lib/auth";
import { useShellData } from "@/lib/shell-data";

import { SENT_HOLD_MS, useAskLocks } from "./AskLocks";

export type AskPhase =
  | { state: "idle" }
  // `retrying`: the agent is not ready yet and the reply is being retried.
  | { state: "sending"; retrying: boolean }
  | { state: "sent" }
  | { state: "error"; message: string };

// Runs one response to an ask. A failure keeps the card as it was, with the reason.
// With a `lockId` under an AskLocksProvider, every surface of the ask shares the phase.
export function useAskResponder(lockId?: string) {
  const { host, token } = useShellData();
  const locks = useAskLocks();
  const shared = lockId !== undefined && locks ? { id: lockId, locks } : null;
  const [local, setLocal] = useState<AskPhase>({ state: "idle" });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const phase = shared ? shared.locks.phase(shared.id) : local;
  const sharedId = shared?.id;
  const setShared = shared?.locks.setPhase;

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const setPhase = useCallback(
    (next: AskPhase) => {
      // The provider times out a shared "sent" itself.
      if (sharedId !== undefined && setShared) setShared(sharedId, next);
      else {
        setLocal(next);
        if (next.state === "sent") timer.current = setTimeout(() => setLocal({ state: "idle" }), SENT_HOLD_MS);
      }
    },
    [sharedId, setShared, setLocal],
  );

  const send = useCallback(
    async (run: (host: string, token: string, retry: RetryOptions) => Promise<void>): Promise<boolean> => {
      setPhase({ state: "sending", retrying: false });
      try {
        await run(host, token, { onRetry: () => setPhase({ state: "sending", retrying: true }) });
        setPhase({ state: "sent" });
        return true;
      } catch (error) {
        if (isAuthError(error)) {
          handleAuthFailure();
        } else {
          setPhase({
            state: "error",
            message: isAgentNotReady(error)
              ? NOT_READY_MESSAGE
              : error instanceof Error
                ? error.message
                : "Could not send",
          });
        }
        return false;
      }
    },
    [host, token, setPhase],
  );

  const busy = phase.state === "sending" || phase.state === "sent";
  return { phase, send, busy };
}
