"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchEvents, isAuthError } from "@waypoint/web-core/api";
import { humaniseBackend, useBackendCatalog } from "@waypoint/web-core/backends";
import { normalizeToolName } from "@waypoint/web-core/events";
import { useAskQuestionState, type AskQuestionController } from "@waypoint/web-core/hooks/useAskQuestionState";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { askUserQuestions } from "@waypoint/web-core/needsYou";
import { decideAsk, decidesInline, decisionView } from "@waypoint/web-core/pendingAskActions";
import {
  approvalBody,
  approvalBodyForTool,
  approvalCopyText,
  type ApprovalBody as ApprovalBodyModel,
} from "@waypoint/web-core/session/approvals";
import { askFormView } from "@waypoint/web-core/session/askQuestion";
import { formatSessionTime } from "@waypoint/web-core/sessionPresentation";
import { sessionHref } from "@waypoint/web-core/shellNav";
import type { PendingApprovalAsk, PendingAsk, PendingPlanAsk, PendingQuestionAsk } from "@waypoint/web-core/types";

import { ApprovalBody } from "@/components/session/attention/ApprovalBody";
import { AskFormView } from "@/components/session/attention/AskForm";
import { DecisionCard } from "@/components/session/attention/DecisionCard";
import { AskControllerProvider, SessionContextProvider } from "@/components/session/SessionContext";
import { useAskLocks } from "@/components/sessions/AskLocks";
import { SourceLine } from "@/components/sessions/SourceLine";
import { ActionChip } from "@/components/ui/ActionChip";
import { withoutRetry } from "@/components/sessions/useAskResponder";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { handleAuthFailure } from "@/lib/auth";
import { usePointerUI } from "@/lib/layout";
import { useSessionsData, useShellData } from "@/lib/shell-data";
import { useFailureToast } from "@/lib/useFailureToast";

import { DetailFrame, DetailState } from "./DetailFrame";
import styles from "./AskDetail.module.css";

const NO_EVENTS: never[] = [];
const noop = () => {};

// The question form on the session's own controller, which retries a not-ready agent.
function QuestionDetail({ ask }: { ask: PendingQuestionAsk }) {
  const { host, token } = useShellData();
  const locks = useAskLocks();
  const toastFailure = useFailureToast();
  const onRequestError = useCallback(
    (error: unknown, fallback: string) => {
      if (isAuthError(error)) handleAuthFailure();
      else toastFailure(error, fallback);
    },
    [toastFailure],
  );
  const controller = useAskQuestionState({
    host,
    token,
    sessionId: ask.session_id,
    snapshot: null,
    events: NO_EVENTS,
    canReattach: false,
    reattach: noop,
    onRequestError,
    retry: true,
  });
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  // The row of this ask waits while the form sends, and the other way round.
  const shared = useMemo<AskQuestionController>(() => {
    const report = async (request: Promise<boolean>) => {
      locks?.setPhase(ask.id, { state: "sending", retrying: false });
      const sent = await request;
      locks?.setPhase(ask.id, sent ? { state: "sent" } : { state: "idle" });
      // The form shows its own failure; once it is gone, nothing else would.
      if (!sent && !mounted.current) toastFailure(null, `Could not reply to ${ask.session_title}`);
      return sent;
    };
    return {
      ...controller,
      updateDraft: (toolUseId, update) => {
        // A row's failed reply is stale once the form says something else.
        if (locks?.phase(ask.id).state === "error") locks.setPhase(ask.id, { state: "idle" });
        controller.updateDraft(toolUseId, update);
      },
      answer: (toolUseId, text, answers) => report(controller.answer(toolUseId, text, answers)),
      cancel: (toolUseId) => report(controller.cancel(toolUseId)),
    };
  }, [controller, locks, ask.id, ask.session_title, toastFailure]);
  const questions = useMemo(() => askUserQuestions(ask), [ask]);
  const toolUseId = ask.question.tool_use_id;
  const { entry } = askFormView(questions, controller.draft(toolUseId));
  // A row's failure shows here without its Retry: the form has a reply of its own.
  const held = locks ? withoutRetry(locks.phase(ask.id)) : undefined;

  return (
    <section className={styles.card} aria-label={ask.title}>
      <h1 className={styles.headline}>{entry?.question ?? ask.title}</h1>
      <AskControllerProvider value={shared}>
        <AskFormView
          toolUseId={toolUseId}
          questions={questions}
          headed
          formState={ask.availability}
          held={held}
          submitKey
        />
      </AskControllerProvider>
    </section>
  );
}

// How far back the request is looked for.
const REQUEST_WINDOW = 20;

type RequestState = { status: "loading" } | { status: "failed" } | { status: "ready"; body: ApprovalBodyModel };

// The request as the transcript has it, so an edit shows its diff and any request
// is whole. Where the transcript does not turn it up, only an ask that carries
// its own request stands in for it.
function useRequestBody(ask: PendingApprovalAsk | PendingPlanAsk): { request: RequestState; retry: () => void } {
  const { host, token } = useShellData();
  const sessionId = ask.session_id;
  const approvalId = ask.kind === "approval" ? ask.approval.approval_id : null;
  const [attempt, setAttempt] = useState(0);
  // What one read turned up: the request, none among the recent events, or no read.
  const [found, setFound] = useState<{ key: string; read: ApprovalBodyModel | "missing" | "failed" } | null>(null);
  const key = `${approvalId} ${attempt}`;

  useEffect(() => {
    if (approvalId === null) return;
    let active = true;
    fetchEvents(host, token, sessionId, { messages: REQUEST_WINDOW })
      .then((page) => {
        const event = page.events.find(
          (entry) => entry.kind === "approval_request" && entry.metadata?.approval_id === approvalId,
        );
        if (active) setFound({ key, read: event ? approvalBody(event) : "missing" });
      })
      .catch((error) => {
        if (!active) return;
        if (isAuthError(error)) handleAuthFailure();
        else setFound({ key, read: "failed" });
      });
    return () => {
      active = false;
    };
  }, [host, token, sessionId, approvalId, key]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  const resolve = (): RequestState => {
    if (ask.kind === "plan_approval") return { status: "ready", body: { kind: "plan", plan: ask.plan.text } };
    if (!found || found.key !== key) return { status: "loading" };
    if (typeof found.read !== "string") return { status: "ready", body: found.read };
    if (found.read === "failed" || !decidesInline(ask)) return { status: "failed" };
    const { tool_name: tool, tool_input: input } = ask.approval;
    return { status: "ready", body: approvalBodyForTool(normalizeToolName(tool), input, ask.body ?? ask.title) };
  };
  return { request: resolve(), retry };
}

function RequestDetail({ ask, cwd, agent }: { ask: PendingApprovalAsk | PendingPlanAsk; cwd: string; agent: string }) {
  const { host, token } = useShellData();
  const { request, retry } = useRequestBody(ask);
  const body = request.status === "ready" ? request.body : null;
  const { actions, supportsNote } = decisionView(ask);
  const context = useMemo(
    () => ({
      host,
      token,
      sessionId: ask.session_id,
      cwd,
      agentName: agent,
      full: false,
      structured: true,
      openFiles: null,
    }),
    [host, token, ask.session_id, cwd, agent],
  );
  const text = ask.body ?? ask.title;

  return (
    <SessionContextProvider value={context}>
      <DecisionCard
        key={ask.id}
        roomy
        title={ask.title}
        copyText={body ? approvalCopyText(body, text) : text}
        copyLabel={ask.kind === "approval" ? "Copy request" : "Copy plan"}
        locked={!body}
        actions={actions.map((action) => ({
          id: action.decision,
          label: action.label,
          primary: action.kind === "approve",
        }))}
        supportsNote={supportsNote}
        availability={ask.availability}
        lockId={ask.id}
        // The card retries a not-ready agent itself.
        decide={(decision, note) => decideAsk(host, token, ask, decision, note, { attempts: 1 })}
      >
        {body ? (
          <ApprovalBody body={body} />
        ) : request.status === "loading" ? (
          <p className={styles.note}>Loading…</p>
        ) : (
          <div className={styles.failed} role="alert">
            <p>{"Couldn't load the request"}</p>
            <ActionChip icon="refresh" onClick={retry}>
              Retry
            </ActionChip>
          </div>
        )}
        {body?.kind === "plan" && !body.plan.trim() ? <p className={styles.note}>No plan text</p> : null}
      </DecisionCard>
    </SessionContextProvider>
  );
}

function AskView({ ask }: { ask: PendingAsk }) {
  const { host, token } = useShellData();
  const { sessions } = useSessionsData();
  const catalog = useBackendCatalog(host, token, null);
  const now = useNow(30_000);
  const pointer = usePointerUI();
  const agent = humaniseBackend(ask.session_backend, catalog);
  const cwd = sessions?.find((session) => session.id === ask.session_id)?.cwd ?? "";
  const source = (
    <SourceLine
      lead={<StatusLamp state="needs" size="sm" flat />}
      name={ask.session_title}
      href={pointer ? sessionHref(ask.session_id) : undefined}
      kind={agent}
      time={<time dateTime={ask.created_at}>{formatSessionTime(ask.created_at, now)}</time>}
    />
  );
  return (
    <DetailFrame source={source} sessionId={ask.session_id}>
      {pointer ? null : <div className={styles.source}>{source}</div>}
      {ask.kind === "question" ? (
        <QuestionDetail key={ask.id} ask={ask} />
      ) : (
        <RequestDetail key={ask.id} ask={ask} cwd={cwd} agent={agent} />
      )}
    </DetailFrame>
  );
}

export function AskDetail({ id, sessionId }: { id: string; sessionId: string }) {
  const { pendingAsks } = useSessionsData();
  if (!pendingAsks) return <DetailState title="Loading…" />;
  const ask = pendingAsks.items.find((item) => item.id === id);
  if (!ask) return <DetailState title="No longer waiting" sessionId={sessionId} />;
  return <AskView ask={ask} />;
}
