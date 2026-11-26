"use client";

import type { SideQuestion } from "@waypoint/web-core/types";

import { ActionChip } from "@/components/ui/ActionChip";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cx } from "@/lib/cx";

import { EventTime } from "../cards/EventTime";
import { ClampedText } from "./ClampedText";
import styles from "./SideQuestionList.module.css";

interface SideQuestionListProps {
  // Newest first.
  questions: SideQuestion[];
  dismissing: ReadonlySet<string>;
  forking: string | null;
  onDismiss: (id: string) => void;
  onFork: (id: string) => void;
}

// Asides the human asked beside the main thread, each with its answer.
export function SideQuestionList({ questions, dismissing, forking, onDismiss, onFork }: SideQuestionListProps) {
  return (
    <ul className={styles.list}>
      {questions.map((aside) => {
        const asking = aside.status === "pending";
        const failed = aside.status === "error";
        return (
          <li key={aside.id} className={styles.item}>
            <div className={styles.meta}>
              <span className={cx(styles.state, failed && styles.failed)}>
                {asking ? (
                  <>
                    <span className={styles.pulse} aria-hidden />
                    Asking…
                  </>
                ) : failed ? (
                  "Failed"
                ) : (
                  "Aside"
                )}
              </span>
              {aside.resumed ? <span>Resumed</span> : null}
              <EventTime ts={aside.created_at} className={styles.time} />
              <IconButton label="Dismiss side question" size="sm" disabled={dismissing.has(aside.id)} onClick={() => onDismiss(aside.id)}>
                <Icon name="close" size={16} />
              </IconButton>
            </div>
            <p className={styles.question}>{aside.question}</p>
            {asking ? null : (
              <ClampedText text={aside.answer ?? aside.error ?? ""} className={cx(styles.answer, failed && styles.failed)} more="Read full aside" />
            )}
            {asking || failed ? null : (
              <div className={styles.foot}>
                <ActionChip icon="external" disabled={forking !== null} onClick={() => onFork(aside.id)}>
                  {forking === aside.id ? "Opening…" : "Open as session"}
                </ActionChip>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
