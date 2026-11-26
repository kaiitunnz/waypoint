"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { answerAskQuestion, cancelAskQuestion, isAuthError } from "../api";
import { isAgentNotReady, retryWhileNotReady } from "../pendingAskActions";
import {
  EMPTY_ASK_DRAFT,
  indexAskQuestionEvidence,
  resolveAskQuestion,
  type AskAnswerEntry,
  type AskDraft,
  type AskQuestionEvidence,
  type AskQuestionResolution,
} from "../session/askQuestion";
import type { EventRecord, PendingQuestionsSnapshot } from "../types";

export type AskOperation = "send" | "cancel";

// Why the last request for a question failed. `notReady`: the agent had not
// opened its dialog yet, so trying again shortly can succeed.
export interface AskFailure {
  operation: AskOperation;
  notReady: boolean;
  message: string;
}

// Session-scoped question state shared by every surface that answers a
// question (transcript card, dock), so all show one draft and one request per
// question.
export interface AskQuestionController {
  // Null until the session's first snapshot arrives.
  snapshot: PendingQuestionsSnapshot | null;
  // What resolved each question, from every loaded event.
  evidence: AskQuestionEvidence;
  draft: (toolUseId: string) => AskDraft;
  updateDraft: (toolUseId: string, update: (draft: AskDraft) => AskDraft) => void;
  operation: (toolUseId: string) => AskOperation | null;
  // True while a request waits for the agent to be ready.
  retrying: (toolUseId: string) => boolean;
  failure: (toolUseId: string) => AskFailure | null;
  answer: (toolUseId: string, text: string, answers: AskAnswerEntry[]) => Promise<boolean>;
  cancel: (toolUseId: string) => Promise<boolean>;
  resolution: (toolUseId: string) => AskQuestionResolution;
  canReattach: boolean;
  reattach: () => void;
}

interface AskQuestionStateOptions {
  host: string;
  token: string;
  sessionId: string;
  snapshot: PendingQuestionsSnapshot | null;
  events: EventRecord[];
  canReattach: boolean;
  reattach: () => void;
  // Gets every failure without `retry`; with it, only auth failures.
  onRequestError: (error: unknown, fallback: string) => void;
  // Retry while the agent is not ready, and keep other failures on the
  // question rather than reporting them.
  retry?: boolean;
}

export function askFailure(error: unknown, operation: AskOperation, fallback: string): AskFailure {
  return {
    operation,
    notReady: isAgentNotReady(error),
    message: error instanceof Error ? error.message : fallback,
  };
}

function withoutKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record };
  delete next[key];
  return next;
}

export function useAskQuestionState({
  host,
  token,
  sessionId,
  snapshot,
  events,
  canReattach,
  reattach,
  onRequestError,
  retry = false,
}: AskQuestionStateOptions): AskQuestionController {
  const [drafts, setDrafts] = useState<Record<string, AskDraft>>({});
  const [operations, setOperations] = useState<Record<string, AskOperation>>({});
  const [retrying, setRetrying] = useState<Record<string, boolean>>({});
  const [failures, setFailures] = useState<Record<string, AskFailure>>({});
  const operationsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    setDrafts({});
    setOperations({});
    setRetrying({});
    setFailures({});
    operationsRef.current.clear();
  }, [sessionId]);

  const evidence = useMemo(() => indexAskQuestionEvidence(events), [events]);

  const run = useCallback(
    async (
      toolUseId: string,
      operation: AskOperation,
      request: () => Promise<void>,
      fallback: string,
    ): Promise<boolean> => {
      if (operationsRef.current.has(toolUseId)) return false;
      operationsRef.current.add(toolUseId);
      setOperations((current) => ({ ...current, [toolUseId]: operation }));
      setFailures((current) => withoutKey(current, toolUseId));
      try {
        if (retry) {
          await retryWhileNotReady(request, {
            onRetry: () => setRetrying((current) => ({ ...current, [toolUseId]: true })),
          });
        } else {
          await request();
        }
        setDrafts((current) => withoutKey(current, toolUseId));
        return true;
      } catch (requestError) {
        if (retry && !isAuthError(requestError)) {
          const failure = askFailure(requestError, operation, fallback);
          setFailures((current) => ({ ...current, [toolUseId]: failure }));
        } else {
          onRequestError(requestError, fallback);
        }
        return false;
      } finally {
        operationsRef.current.delete(toolUseId);
        setOperations((current) => withoutKey(current, toolUseId));
        setRetrying((current) => withoutKey(current, toolUseId));
      }
    },
    [onRequestError, retry],
  );

  return useMemo<AskQuestionController>(
    () => ({
      snapshot,
      evidence,
      draft: (toolUseId) => drafts[toolUseId] ?? EMPTY_ASK_DRAFT,
      updateDraft: (toolUseId, update) =>
        setDrafts((current) => ({
          ...current,
          [toolUseId]: update(current[toolUseId] ?? EMPTY_ASK_DRAFT),
        })),
      operation: (toolUseId) => operations[toolUseId] ?? null,
      retrying: (toolUseId) => retrying[toolUseId] ?? false,
      failure: (toolUseId) => failures[toolUseId] ?? null,
      answer: (toolUseId, text, answers) =>
        run(
          toolUseId,
          "send",
          () => answerAskQuestion(host, token, sessionId, text, toolUseId, answers),
          "failed to send answer",
        ),
      cancel: (toolUseId) =>
        run(
          toolUseId,
          "cancel",
          () => cancelAskQuestion(host, token, sessionId, toolUseId),
          "failed to cancel question",
        ),
      resolution: (toolUseId) => resolveAskQuestion(toolUseId, evidence),
      canReattach,
      reattach,
    }),
    [canReattach, drafts, evidence, failures, host, operations, reattach, retrying, run, sessionId, snapshot, token],
  );
}
