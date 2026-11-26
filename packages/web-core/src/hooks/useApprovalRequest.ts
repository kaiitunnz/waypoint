"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { isAuthError } from "../api";
import {
  approvalRequest,
  askedCall,
  NO_CALL,
  readTranscript,
  type ApprovalRequest,
  type AskCall,
  type TranscriptRead,
} from "../pendingAskActions";
import { shownRequest, type AskSession } from "../session/wholeRequest";
import type { PendingApprovalAsk, PendingAsk, PendingPlanAsk } from "../types";

// The transcript around the request `requestId` names, from before it to the present;
// null until it has been read, or where there is none to read. It is read again when
// the session records something. `onAuthFailure` must be stable.
function useTranscriptRead(
  host: string,
  token: string,
  ask: PendingAsk | null,
  requestId: string | null,
  revision: string,
  onAuthFailure: () => void,
): { read: TranscriptRead | null; retry: () => void } {
  const sessionId = ask?.session_id ?? "";
  const sequence = ask?.sequence ?? 0;
  const key = `${sessionId}:${requestId}:${sequence}`;
  const [result, setResult] = useState<{ key: string; read: TranscriptRead } | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (requestId === null) return;
    let active = true;
    readTranscript(host, token, sessionId, requestId, sequence)
      .then((read) => {
        if (active) setResult({ key, read });
      })
      .catch((error) => {
        if (!active) return;
        if (isAuthError(error)) onAuthFailure();
        else setResult({ key, read: "failed" });
      });
    return () => {
      active = false;
    };
  }, [host, token, sessionId, requestId, sequence, key, revision, attempt, onAuthFailure]);

  const retry = useCallback(() => {
    setResult(null);
    setAttempt((value) => value + 1);
  }, []);

  // A read of another request is never shown for this one.
  return { read: result?.key === key ? result.read : null, retry };
}

// A pending approval's request, as its detail shows it.
export function useApprovalRequest(
  host: string,
  token: string,
  ask: PendingApprovalAsk | PendingPlanAsk,
  session: AskSession,
  onAuthFailure: () => void,
): { request: ApprovalRequest; retry: () => void } {
  const requestId =
    ask.kind === "approval"
      ? ask.approval.approval_id
      : ask.plan.resolve_via === "approve"
        ? ask.plan.plan_id
        : null;
  const { read, retry } = useTranscriptRead(host, token, ask, requestId, session.revision, onAuthFailure);
  const { cwd, fromScreen } = session;
  const request = useMemo(() => approvalRequest(ask, read, { cwd, fromScreen }), [ask, read, cwd, fromScreen]);
  return { request, retry };
}

// The call behind a pending ask, as a row or card needs it: from the ask, else from
// the transcript where the ask cannot say.
export function useAskCall(
  host: string,
  token: string,
  ask: PendingAsk | null,
  session: AskSession,
  onAuthFailure: () => void,
): AskCall {
  const asked = ask ? askedCall(ask, session.fromScreen) : NO_CALL;
  const requestId = asked === null && ask?.kind === "approval" ? ask.approval.approval_id : null;
  const { read } = useTranscriptRead(host, token, ask, requestId, session.revision, onAuthFailure);
  if (asked) return asked;
  if (read === null) return { input: null, reading: true };
  if (typeof read === "string") return NO_CALL;
  const shown = shownRequest(read.request, read.events, session);
  return shown.whole ? { input: shown.input, reading: false } : NO_CALL;
}
