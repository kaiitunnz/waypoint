"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { cancelHeldMessages, isAuthError, releaseHeldMessages } from "@waypoint/web-core/api";
import {
  approvalDecisionsFor,
  supportsApprovalNote,
  supportsStructuredApproval,
  type BackendCatalog,
} from "@waypoint/web-core/backends";
import type { PlanViewModel } from "@waypoint/web-core/events";
import { findPendingApprovals } from "@waypoint/web-core/session/approvals";
import { buildMessageQueue, type QueueAction, type QueueItem } from "@waypoint/web-core/session/messageQueue";
import type { SideQuestionState } from "@waypoint/web-core/session/sideQuestions";
import type {
  EventRecord,
  HeldMessage,
  MessageSchedule,
  PendingQuestionsSnapshot,
  SessionRecord,
} from "@waypoint/web-core/types";

import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";

import { useSessionContext } from "../SessionContext";
import { ApprovalDock } from "./ApprovalDock";
import styles from "./AttentionStack.module.css";
import { PlanApprovalDock } from "./PlanApprovalDock";
import { QuestionDock } from "./QuestionDock";
import { QueueDock } from "./QueueDock";
import { SideQuestionDock } from "./SideQuestionDock";

interface AttentionStackProps {
  session: SessionRecord;
  catalog: BackendCatalog;
  events: EventRecord[];
  // The plan waiting for approval, when the session is in plan mode.
  plan: PlanViewModel | null;
  pendingQuestions: PendingQuestionsSnapshot | null;
  heldMessages: HeldMessage[];
  scheduled: { messages: MessageSchedule[]; cancel: (id: string) => Promise<void> };
  sideQuestions: SideQuestionState;
  sideQuestionArrivals: number;
  onSession: (session: SessionRecord) => void;
  onRevealQuestion: (toolUseId: string) => void;
}

// What waits on the human, above the composer: an approval, else the plan,
// else the open questions; then one-line strips for the message queue and
// side questions.
export function AttentionStack({
  session,
  catalog,
  events,
  plan,
  pendingQuestions,
  heldMessages,
  scheduled,
  sideQuestions,
  sideQuestionArrivals,
  onSession,
  onRevealQuestion,
}: AttentionStackProps) {
  const { host, token, sessionId } = useSessionContext();
  const { toast } = useToast();
  const root = useRef<HTMLDivElement>(null);
  // Held here, where it survives the side-question strip mounting with its first aside:
  // a live aside's arrival opens the panel.
  const [asidesOpen, setAsidesOpen] = useState(false);
  const [seenArrivals, setSeenArrivals] = useState(sideQuestionArrivals);
  if (sideQuestionArrivals !== seenArrivals) {
    setSeenArrivals(sideQuestionArrivals);
    if (sideQuestionArrivals > seenArrivals) setAsidesOpen(true);
  }

  const approvals = useMemo(
    () => (supportsStructuredApproval(session.transport, catalog) ? findPendingApprovals(events) : []),
    [session.transport, catalog, events],
  );
  const questions = pendingQuestions?.questions ?? [];
  const queue = useMemo(() => buildMessageQueue(heldMessages, scheduled.messages), [heldMessages, scheduled.messages]);

  // Toasts sit above the stack as they do above the composer.
  useEffect(() => {
    const node = root.current;
    if (!node) return;
    const page = document.documentElement;
    const observer = new ResizeObserver(() => page.style.setProperty("--dock-height", `${node.offsetHeight}px`));
    observer.observe(node);
    return () => {
      observer.disconnect();
      page.style.removeProperty("--dock-height");
    };
  }, []);

  const report = (error: unknown, fallback: string) => {
    if (isAuthError(error)) handleAuthFailure();
    else toast({ message: error instanceof Error ? error.message : fallback, tone: "error" });
  };
  const run = async (request: () => Promise<void>, fallback: string) => {
    try {
      await request();
    } catch (error) {
      report(error, fallback);
    }
  };
  const onAction = (item: QueueItem, action: QueueAction) => {
    if (item.kind === "scheduled") return scheduled.cancel(item.id);
    const target = { heldId: item.id };
    return action === "release"
      ? run(() => releaseHeldMessages(host, token, target), "Could not release the message")
      : run(() => cancelHeldMessages(host, token, target), "Could not cancel the message");
  };

  const decision =
    approvals.length > 0 ? (
      <ApprovalDock
        approvals={approvals}
        decisions={approvalDecisionsFor(session.backend, catalog)}
        supportsNote={supportsApprovalNote(session.backend, catalog)}
      />
    ) : plan ? (
      <PlanApprovalDock plan={plan} onSession={onSession} />
    ) : questions.length > 0 ? (
      <QuestionDock questions={questions} onReveal={onRevealQuestion} />
    ) : null;
  const strips = queue.length > 0 || sideQuestions.items.size > 0;

  return (
    <div ref={root} className={styles.stack}>
      {decision}
      {strips ? (
        <div className={styles.strips}>
          <QueueDock
            items={queue}
            focus={Boolean(session.focus)}
            onAction={onAction}
            onReleaseAll={() => run(() => releaseHeldMessages(host, token, { sessionId }), "Could not release the messages")}
            onCancelAll={() => run(() => cancelHeldMessages(host, token, { sessionId }), "Could not cancel the messages")}
          />
          <SideQuestionDock questions={sideQuestions.items.values()} open={asidesOpen} onOpenChange={setAsidesOpen} />
        </div>
      ) : null}
    </div>
  );
}
