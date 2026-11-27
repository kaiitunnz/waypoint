"use client";

import { useMemo } from "react";

import { humaniseBackend } from "@waypoint/web-core/backends";
import { useApprovalRequest } from "@waypoint/web-core/hooks/useApprovalRequest";
import { useAskQuestionState, type AskQuestionController } from "@waypoint/web-core/hooks/useAskQuestionState";
import { useNow } from "@waypoint/web-core/hooks/useNow";
import { askUserQuestions } from "@waypoint/web-core/needsYou";
import { answersScreenDialog, decideAsk, decisionView } from "@waypoint/web-core/pendingAskActions";
import { approvalCopyText } from "@waypoint/web-core/session/approvals";
import { askFormView } from "@waypoint/web-core/session/askQuestion";
import { formatSessionTime } from "@waypoint/web-core/sessionPresentation";
import { requestText, type AskSession } from "@waypoint/web-core/session/wholeRequest";
import { sessionHref, sessionTerminalHref } from "@waypoint/web-core/shellNav";
import type { PendingApprovalAsk, PendingAsk, PendingPlanAsk, PendingQuestionAsk } from "@waypoint/web-core/types";

import { ApprovalBody } from "@/components/session/attention/ApprovalBody";
import { AskFormView } from "@/components/session/attention/AskForm";
import { DecisionCard } from "@/components/session/attention/DecisionCard";
import { AskControllerProvider, SessionContextProvider } from "@/components/session/SessionContext";
import { useAskLocks } from "@/components/sessions/AskLocks";
import { SourceLine } from "@/components/sessions/SourceLine";
import { useAskResponder } from "@/components/sessions/useAskResponder";
import { useAskSessions } from "@/components/sessions/useAskSessions";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { handleAuthFailure } from "@/lib/auth";
import { usePointerUI } from "@/lib/layout";
import { useSessionsData, useShellData } from "@/lib/shell-data";
import { useFailureToast } from "@/lib/useFailureToast";
import { useMountedRef } from "@/lib/useMountedRef";

import { DetailFrame, DetailState } from "./DetailFrame";
import { LoadFailed } from "./LoadFailed";
import { RequestFields } from "./RequestFields";
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

interface RequestDetailProps {
  ask: PendingApprovalAsk | PendingPlanAsk;
  session: AskSession;
  agent: string;
}

function RequestDetail({ ask, session, agent }: RequestDetailProps) {
  const { host, token } = useShellData();
  const { request, retry } = useApprovalRequest(host, token, ask, session, handleAuthFailure);
  const shown = request.status === "ready" ? request : null;
  // A tool without a view of its own is shown by its fields, and so is what was only
  // read off the screen.
  const view =
    shown && !shown.unverified && !(shown.body.kind === "raw" && shown.fields.tool_input) ? shown.body : null;
  const { actions, supportsNote } = decisionView(ask);
  const context = useDetailContext(ask.session_id, session.cwd, agent);
  const text = ask.body ?? ask.title;
  const copyText = view ? approvalCopyText(view, text) : shown ? requestText(shown.fields) : text;

  return (
    <SessionContextProvider value={context}>
      <DecisionCard
        roomy
        title={ask.title}
        copyText={copyText}
        copyLabel={ask.kind === "plan_approval" && !shown?.unverified ? "Copy plan" : "Copy request"}
        // Nothing is decided on part of a request.
        locked={!shown?.whole}
        actions={actions.map((action) => ({
          id: action.decision,
          label: action.label,
          primary: action.decision === "accept",
        }))}
        supportsNote={supportsNote}
        availability={ask.availability}
        lockId={ask.id}
        // The card retries a not-ready agent itself.
        decide={(decision, note) => decideAsk(host, token, ask, decision, note, { attempts: 1 })}
      >
        {shown ? (
          <>
            {shown.warnings.map((warning) => (
              <p key={warning} className={styles.warn}>
                {warning}
              </p>
            ))}
            {view ? <ApprovalBody body={view} whole /> : null}
            <RequestFields fields={shown.fields} />
            {shown.lockNote ? <p className={styles.note}>{shown.lockNote}</p> : null}
          </>
        ) : request.status === "loading" ? (
          <p className={styles.note}>Loading…</p>
        ) : (
          <LoadFailed message={"Couldn't load the request"} onRetry={retry} />
        )}
      </DecisionCard>
    </SessionContextProvider>
  );
}

function AskView({ ask }: { ask: PendingAsk }) {
  const { catalog } = useShellData();
  const now = useNow(30_000);
  const pointer = usePointerUI();
  const agent = humaniseBackend(ask.session_backend, catalog);
  const askSession = useAskSessions();
  const session = askSession(ask.session_id);
  // An approval read off the screen is not decided here.
  const terminal = ask.kind !== "question" && answersScreenDialog(ask, session.fromScreen);
  const href = terminal ? sessionTerminalHref(ask.session_id) : sessionHref(ask.session_id);
  const source = (
    <SourceLine
      lead={<StatusLamp state="needs" size="sm" flat />}
      name={ask.session_title}
      href={pointer ? href : undefined}
      kind={agent}
      time={<time dateTime={ask.created_at}>{formatSessionTime(ask.created_at, now)}</time>}
    />
  );
  return (
    <DetailFrame source={source} sessionId={ask.session_id} terminal={terminal}>
      {ask.kind === "question" ? (
        <QuestionDetail ask={ask} />
      ) : (
        <RequestDetail ask={ask} session={session} agent={agent} />
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
