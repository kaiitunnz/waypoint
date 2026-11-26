"use client";

import { useCallback, useEffect, useState } from "react";

import { fetchEvents, isAuthError } from "../api";
import { approvalRequest, type ApprovalRead, type ApprovalRequest } from "../pendingAskActions";
import { wholeApprovalBody } from "../session/approvals";
import type { PendingApprovalAsk, PendingPlanAsk } from "../types";

// A pending approval's request, read from the session's transcript. One instance
// serves one ask. `onAuthFailure` must be stable.
export function useApprovalRequest(
  host: string,
  token: string,
  ask: PendingApprovalAsk | PendingPlanAsk,
  onAuthFailure: () => void,
): { request: ApprovalRequest; retry: () => void } {
  const sessionId = ask.session_id;
  const approvalId = ask.kind === "approval" ? ask.approval.approval_id : null;
  const sequence = ask.sequence;
  const [read, setRead] = useState<ApprovalRead | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (approvalId === null) return;
    let active = true;
    // The page ending at the request: a pending ask can be many messages old.
    fetchEvents(host, token, sessionId, { beforeSequence: sequence + 1, messages: 1 })
      .then((page) => {
        const event = page.events.find(
          (entry) => entry.kind === "approval_request" && entry.metadata?.approval_id === approvalId,
        );
        if (active) setRead(event ? wholeApprovalBody(event) : "missing");
      })
      .catch((error) => {
        if (!active) return;
        if (isAuthError(error)) onAuthFailure();
        else setRead("failed");
      });
    return () => {
      active = false;
    };
  }, [host, token, sessionId, approvalId, sequence, attempt, onAuthFailure]);

  const retry = useCallback(() => {
    setRead(null);
    setAttempt((value) => value + 1);
  }, []);

  return { request: approvalRequest(ask, read), retry };
}
