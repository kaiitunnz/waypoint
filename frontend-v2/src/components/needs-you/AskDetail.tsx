"use client";

import { useMemo } from "react";

import { humaniseBackend, useBackendCatalog } from "@waypoint/web-core/backends";
import { useApprovalRequest } from "@waypoint/web-core/hooks/useApprovalRequest";
import { useAskQuestionState, type AskQuestionController } from "@waypoint/web-core/hooks/useAskQuestionState";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { askUserQuestions } from "@waypoint/web-core/needsYou";
import { decideAsk, decisionView } from "@waypoint/web-core/pendingAskActions";
import { approvalCopyText } from "@waypoint/web-core/session/approvals";
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
import { useAskResponder } from "@/components/sessions/useAskResponder";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { handleAuthFailure } from "@/lib/auth";
import { usePointerUI } from "@/lib/layout";
import { useSessionsData, useShellData } from "@/lib/shell-data";
import { useFailureToast } from "@/lib/useFailureToast";
import { useMountedRef } from "@/lib/useMountedRef";

import { DetailFrame, DetailState } from "./DetailFrame";
import { LoadFailed } from "./LoadFailed";
import { useDetailContext } from "./useDetailContext";
import styles from "./AskDetail.module.css";

const NO_EVENTS: never[] = [];
const noop = () => {};

function QuestionDetail({ ask }: { ask: PendingQuestionAsk }) {
  const { host, token } = useShellData();
  const locks = useAskLocks();
  // The phase of a reply the row sent, without its Retry: the form has a reply of its own.
  const { phase: held, dismiss } = useAskResponder(ask.id);
  const toastFailure = useFailureToast();
  const controller = useAskQuestionState({
    host,
    token,
    sessionId: ask.session_id,
    snapshot: null,
    events: NO_EVENTS,
    canReattach: false,
    reattach: noop,
    onRequestError: handleAuthFailure,
    retry: true,
  });
  const mounted = useMountedRef();
  // Reports the form's reply to AskLocks, so the ask's row waits while the form sends.
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
        dismiss();
        controller.updateDraft(toolUseId, update);
      },
      answer: (toolUseId, text, answers) => report(controller.answer(toolUseId, text, answers)),
      cancel: (toolUseId) => report(controller.cancel(toolUseId)),
    };
  }, [controller, dismiss, locks, mounted, ask.id, ask.session_title, toastFailure]);
  const questions = useMemo(() => askUserQuestions(ask), [ask]);
  const toolUseId = ask.question.tool_use_id;
  const { entry } = askFormView(questions, controller.draft(toolUseId));

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

function RequestDetail({ ask, cwd, agent }: { ask: PendingApprovalAsk | PendingPlanAsk; cwd: string; agent: string }) {
  const { host, token } = useShellData();
  const { request, retry } = useApprovalRequest(host, token, ask, cwd, handleAuthFailure);
  const body = request.status === "ready" ? request.body : null;
  const { actions, supportsNote } = decisionView(ask);
  const context = useDetailContext(ask.session_id, cwd, agent);
  const text = ask.body ?? ask.title;

  return (
    <SessionContextProvider value={context}>
      <DecisionCard
        roomy
        title={ask.title}
        copyText={body ? approvalCopyText(body, text) : text}
        copyLabel={ask.kind === "approval" ? "Copy request" : "Copy plan"}
        // Nothing is decided on part of a request.
        locked={!(request.status === "ready" && request.whole)}
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
          <ApprovalBody body={body} whole />
        ) : request.status === "loading" ? (
          <p className={styles.note}>Loading…</p>
        ) : (
          <LoadFailed message={"Couldn't load the request"} onRetry={retry} />
        )}
        {request.status === "ready" && !request.whole ? (
          <p className={styles.note}>
            {request.body.kind === "plan" ? "No plan text" : "The whole request isn't available here"}
          </p>
        ) : null}
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
      {ask.kind === "question" ? <QuestionDetail ask={ask} /> : <RequestDetail ask={ask} cwd={cwd} agent={agent} />}
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
