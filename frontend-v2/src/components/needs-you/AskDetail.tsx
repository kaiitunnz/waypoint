"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchEvents, isAuthError } from "@waypoint/web-core/api";
import { humaniseBackend, useBackendCatalog } from "@waypoint/web-core/backends";
import { normalizeToolName } from "@waypoint/web-core/events";
import { useAskQuestionState, type AskQuestionController } from "@waypoint/web-core/hooks/useAskQuestionState";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { askUserQuestions } from "@waypoint/web-core/needsYou";
import { decideAsk, decisionView } from "@waypoint/web-core/pendingAskActions";
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
  // The row of this ask waits while the form sends, and the other way round.
  const shared = useMemo<AskQuestionController>(() => {
    const report = async (request: Promise<boolean>) => {
      locks?.setPhase(ask.id, { state: "sending", retrying: false });
      const sent = await request;
      locks?.setPhase(ask.id, sent ? { state: "sent" } : { state: "idle" });
      return sent;
    };
    return {
      ...controller,
      answer: (toolUseId, text, answers) => report(controller.answer(toolUseId, text, answers)),
      cancel: (toolUseId) => report(controller.cancel(toolUseId)),
    };
  }, [controller, locks, ask.id]);
  const questions = useMemo(() => askUserQuestions(ask), [ask]);
  const toolUseId = ask.question.tool_use_id;
  const { entry } = askFormView(questions, controller.draft(toolUseId));
  const held = locks?.phase(ask.id);

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

// The request as the transcript has it, so an edit shows its diff and a long
// command is whole. Null until read.
function useRequestBody(ask: PendingApprovalAsk | PendingPlanAsk): { body: ApprovalBodyModel; cut: boolean } | null {
  const { host, token } = useShellData();
  const sessionId = ask.session_id;
  const approvalId = ask.kind === "approval" ? ask.approval.approval_id : null;
  const [found, setFound] = useState<{ id: string; body: ApprovalBodyModel | null } | null>(null);

  useEffect(() => {
    if (approvalId === null) return;
    let active = true;
    fetchEvents(host, token, sessionId, { messages: 5 })
      .then((page) => {
        const event = page.events.find(
          (entry) => entry.kind === "approval_request" && entry.metadata?.approval_id === approvalId,
        );
        if (active) setFound({ id: approvalId, body: event ? approvalBody(event) : null });
      })
      .catch((error) => {
        if (!active) return;
        if (isAuthError(error)) handleAuthFailure();
        else setFound({ id: approvalId, body: null });
      });
    return () => {
      active = false;
    };
  }, [host, token, sessionId, approvalId]);

  if (ask.kind === "plan_approval") return { body: { kind: "plan", plan: ask.plan.text }, cut: false };
  if (!found || found.id !== approvalId) return null;
  if (found.body) return { body: found.body, cut: false };
  const { tool_name: tool, tool_input: input, tool_input_truncated: cut } = ask.approval;
  return { body: approvalBodyForTool(normalizeToolName(tool), input, ask.body ?? ask.title), cut };
}

function RequestDetail({ ask, cwd, agent }: { ask: PendingApprovalAsk | PendingPlanAsk; cwd: string; agent: string }) {
  const { host, token } = useShellData();
  const request = useRequestBody(ask);
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
        copyText={request ? approvalCopyText(request.body, text) : text}
        copyLabel={ask.kind === "approval" ? "Copy request" : "Copy plan"}
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
        {request ? <ApprovalBody body={request.body} /> : <p className={styles.note}>Loading…</p>}
        {request?.cut ? <p className={styles.note}>Shortened</p> : null}
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
      href={sessionHref(ask.session_id)}
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
