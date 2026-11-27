"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchEvents, isAuthError } from "../api";
import { approvalRequest, type ApprovalRequest, type RequestRead } from "../pendingAskActions";
import type { AskSession } from "../session/wholeRequest";
import type { PendingApprovalAsk, PendingPlanAsk } from "../types";

// A pending approval's request, read from the session's transcript. `onAuthFailure`
// must be stable.
export function useApprovalRequest(
  host: string,
  token: string,
  ask: PendingApprovalAsk | PendingPlanAsk,
  session: AskSession,
  onAuthFailure: () => void,
): { request: ApprovalRequest; retry: () => void } {
  const sessionId = ask.session_id;
  const requestId =
    ask.kind === "approval"
      ? ask.approval.approval_id
      : ask.plan.resolve_via === "approve"
        ? ask.plan.plan_id
        : null;
  const sequence = ask.sequence;
  const key = `${sessionId}:${requestId}:${sequence}`;
  const [result, setResult] = useState<{ key: string; read: RequestRead } | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (requestId === null) return;
    let active = true;
    // The page ending at the request: a pending ask can be many messages old.
    fetchEvents(host, token, sessionId, { beforeSequence: sequence + 1, messages: 1 })
      .then((page) => {
        const event = page.events.find(
          (entry) => entry.kind === "approval_request" && entry.metadata?.approval_id === requestId,
        );
        if (active) setResult({ key, read: event ?? "missing" });
      })
      .catch((error) => {
        if (!active) return;
        if (isAuthError(error)) onAuthFailure();
        else setResult({ key, read: "failed" });
      });
    return () => {
      active = false;
    };
  }, [host, token, sessionId, requestId, sequence, key, attempt, onAuthFailure]);

  const retry = useCallback(() => {
    setResult(null);
    setAttempt((value) => value + 1);
  }, []);

  // A read of another request is never shown for this one.
  const read = result?.key === key ? result.read : null;
  const { cwd, fromScreen } = session;
  const request = useMemo(() => approvalRequest(ask, read, { cwd, fromScreen }), [ask, read, cwd, fromScreen]);
  return { request, retry };
}
