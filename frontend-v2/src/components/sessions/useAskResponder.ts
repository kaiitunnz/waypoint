"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { isAuthError } from "@waypoint/web-core/api";
import { isAgentNotReady, NOT_READY_MESSAGE, type RetryOptions } from "@waypoint/web-core/pendingAskActions";

import { handleAuthFailure } from "@/lib/auth";
import { useShellData } from "@/lib/shell-data";

export type AskPhase =
  | { state: "idle" }
  // `retrying`: the agent is not ready yet and the reply is being retried.
  | { state: "sending"; retrying: boolean }
  | { state: "sent" }
  | { state: "error"; message: string };

// An answered ask leaves the feed when the agent closes it; if it is still listed
// after this, the card takes input again.
const SENT_HOLD_MS = 10_000;

// Runs one response to an ask. A failure keeps the card as it was, with the reason.
export function useAskResponder() {
  const { host, token } = useShellData();
  const [phase, setPhase] = useState<AskPhase>({ state: "idle" });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const send = useCallback(
    async (run: (host: string, token: string, retry: RetryOptions) => Promise<void>): Promise<boolean> => {
      setPhase({ state: "sending", retrying: false });
      try {
        await run(host, token, { onRetry: () => setPhase({ state: "sending", retrying: true }) });
        setPhase({ state: "sent" });
        timer.current = setTimeout(() => setPhase({ state: "idle" }), SENT_HOLD_MS);
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
    [host, token],
  );

  const busy = phase.state === "sending" || phase.state === "sent";
  return { phase, send, busy };
}
