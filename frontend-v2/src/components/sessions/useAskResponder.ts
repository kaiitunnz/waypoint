"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { isAuthError } from "@waypoint/web-core/api";
import { isAgentNotReady, NOT_READY_MESSAGE, type RetryOptions } from "@waypoint/web-core/pendingAskActions";

import { handleAuthFailure } from "@/lib/auth";
import { useShellData } from "@/lib/shell-data";

import { SENT_HOLD_MS, useAskLocks } from "./AskLocks";

// `action` names which reply is in flight or went through.
export type AskPhase =
  | { state: "idle" }
  // `retrying`: the agent is not ready yet and the reply is being retried.
  | { state: "sending"; retrying: boolean; action?: string }
  | { state: "sent"; action?: string }
  // `retry` sends the very reply that failed. `owner` names the surface that sent
  // it, the only one to offer the retry.
  | { state: "error"; message: string; owner?: string; retry?: () => void };

// A failure as a surface that did not send the reply shows it.
export function withoutRetry(phase: AskPhase): AskPhase {
  return phase.state === "error" ? { state: "error", message: phase.message } : phase;
}

// Runs one response to an ask. A failure keeps the card as it was, with the reason.
// With a `lockId` under an AskLocksProvider, every surface of the ask shares the phase.
export function useAskResponder(lockId?: string) {
  const { host, token } = useShellData();
  const owner = useId();
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
    async function attempt(
      run: (host: string, token: string, retry: RetryOptions) => Promise<void>,
      action?: string,
    ): Promise<boolean> {
      setPhase({ state: "sending", retrying: false, action });
      try {
        await run(host, token, { onRetry: () => setPhase({ state: "sending", retrying: true, action }) });
        setPhase({ state: "sent", action });
        return true;
      } catch (error) {
        if (isAuthError(error)) {
          handleAuthFailure();
        } else {
          setPhase({
            state: "error",
            owner,
            message: isAgentNotReady(error)
              ? NOT_READY_MESSAGE
              : error instanceof Error
                ? error.message
                : "Could not send",
            retry: () => void attempt(run, action),
          });
        }
        return false;
      }
    },
    [host, token, owner, setPhase],
  );

  // A reply that has been edited since makes the failed one stale.
  const dismiss = useCallback(() => {
    if (phase.state === "error") setPhase({ state: "idle" });
  }, [phase.state, setPhase]);

  const busy = phase.state === "sending" || phase.state === "sent";
  const own = phase.state !== "error" || phase.owner === owner;
  return { phase: own ? phase : withoutRetry(phase), send, busy, dismiss };
}
