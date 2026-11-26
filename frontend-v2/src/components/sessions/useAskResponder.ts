"use client";

import { useCallback, useId } from "react";

import { isAuthError } from "@waypoint/web-core/api";
import { isAgentNotReady, NOT_READY_MESSAGE, type RetryOptions } from "@waypoint/web-core/pendingAskActions";

import { handleAuthFailure } from "@/lib/auth";
import { useShellData } from "@/lib/shell-data";

import { useAskLocks, useAskPhases } from "./AskLocks";

// `action` names which reply is in flight or went through.
export type AskPhase =
  | { state: "idle" }
  // `retrying`: the agent is not ready yet and the reply is being retried.
  | { state: "sending"; retrying: boolean; action?: string }
  | { state: "sent"; action?: string }
  | {
      state: "error";
      message: string;
      // The surface that sent the reply, the only one to offer its retry.
      owner?: string;
      // Sends the failed reply again.
      retry?: () => void;
    };

// Runs one response to an ask. A failure keeps the card as it was, with the reason.
// With a `lockId` under an AskLocksProvider, every surface of the ask shares the phase.
export function useAskResponder(lockId?: string) {
  const { host, token } = useShellData();
  const owner = useId();
  const shared = useAskLocks();
  const own = useAskPhases();
  const [locks, id] = shared && lockId !== undefined ? [shared, lockId] : [own, owner];
  const { setPhase: setById } = locks;
  const setPhase = useCallback((next: AskPhase) => setById(id, next), [setById, id]);
  const phase = locks.phase(id);

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
  const offered: AskPhase =
    phase.state === "error" && phase.owner !== owner ? { state: "error", message: phase.message } : phase;
  return { phase: offered, send, busy, dismiss };
}
