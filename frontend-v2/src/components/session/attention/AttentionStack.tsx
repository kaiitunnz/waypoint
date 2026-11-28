"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { approvalDecisionsFor, supportsApprovalNote, type BackendCatalog } from "@waypoint/web-core/backends";
import type { PlanViewModel } from "@waypoint/web-core/events";
import { buildMessageQueue } from "@waypoint/web-core/session/messageQueue";
import type { SideQuestionState } from "@waypoint/web-core/session/sideQuestions";
import type { EventRecord, HeldMessage, PendingQuestionsSnapshot, SessionRecord } from "@waypoint/web-core/types";

import { ApprovalDock } from "./ApprovalDock";
import styles from "./AttentionStack.module.css";
import { PlanApprovalDock } from "./PlanApprovalDock";
import { QuestionDock } from "./QuestionDock";
import { QueueDock } from "./QueueDock";
import { SideQuestionDock } from "./SideQuestionDock";
import { useQueueHandlers, type ScheduledMessages } from "./useQueueHandlers";

interface AttentionStackProps {
  session: SessionRecord;
  catalog: BackendCatalog;
  approvals: EventRecord[];
  // Shown only while no approval waits.
  plan: PlanViewModel | null;
  pendingQuestions: PendingQuestionsSnapshot | null;
  heldMessages: HeldMessage[];
  scheduled: ScheduledMessages;
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
  approvals,
  plan,
  pendingQuestions,
  heldMessages,
  scheduled,
  sideQuestions,
  sideQuestionArrivals,
  onSession,
  onRevealQuestion,
}: AttentionStackProps) {
  const queueHandlers = useQueueHandlers(scheduled);
  const root = useRef<HTMLDivElement>(null);
  // Held here, where it survives the side-question strip mounting with its first aside:
  // a live aside's arrival opens the panel, leaving the focus where the human is typing.
  const [asides, setAsides] = useState<"closed" | "arrived" | "opened">("closed");
  const [seenArrivals, setSeenArrivals] = useState(sideQuestionArrivals);
  if (sideQuestionArrivals !== seenArrivals) {
    setSeenArrivals(sideQuestionArrivals);
    if (sideQuestionArrivals > seenArrivals && asides === "closed") setAsides("arrived");
  }
  if (sideQuestions.items.size === 0 && asides !== "closed") setAsides("closed");
  const questions = pendingQuestions?.questions ?? [];
  const queue = useMemo(() => buildMessageQueue(heldMessages, scheduled.messages), [heldMessages, scheduled.messages]);

  // Toasts sit above the stack as they do above the composer.
  useEffect(() => {
    const node = root.current;
    if (!node) return;
    const page = document.documentElement;
    const observer = new ResizeObserver(() => page.style.setProperty("--attention-height", `${node.offsetHeight}px`));
    observer.observe(node);
    return () => {
      observer.disconnect();
      page.style.removeProperty("--attention-height");
    };
  }, []);

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
          <QueueDock items={queue} focus={Boolean(session.focus)} {...queueHandlers} />
          <SideQuestionDock
            questions={sideQuestions.items.values()}
            open={asides !== "closed"}
            autoFocus={asides === "opened"}
            onOpenChange={(open) => setAsides(open ? "opened" : "closed")}
          />
        </div>
      ) : null}
    </div>
  );
}
