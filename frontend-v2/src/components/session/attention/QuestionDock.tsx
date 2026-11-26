"use client";

import { useEffect, useId, useState } from "react";

import { parseAskUserQuestion, pendingQuestionPrompt } from "@waypoint/web-core/session/askQuestion";
import type { PendingQuestion } from "@waypoint/web-core/types";

import { ActionChip } from "@/components/ui/ActionChip";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cx } from "@/lib/cx";

import { EventTime } from "../cards/EventTime";
import { useSessionContext } from "../SessionContext";
import { AskForm } from "./AskForm";
import styles from "./QuestionDock.module.css";

interface QuestionDockProps {
  // Oldest first.
  questions: PendingQuestion[];
  onReveal: (toolUseId: string) => void;
}

// The session's open questions: one line until the human opens it, then the
// answer form, paging between questions when several wait.
export function QuestionDock({ questions, onReveal }: QuestionDockProps) {
  const { agentName } = useSessionContext();
  const [expanded, setExpanded] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const bodyId = useId();

  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const count = questions.length;
  const found = questions.findIndex((question) => question.tool_use_id === openId);
  const index = found === -1 ? 0 : found;
  const current = questions[index];
  if (!current) return null;
  const parsed = parseAskUserQuestion(current.event);
  const prompt = pendingQuestionPrompt(current);
  const step = (by: number) => setOpenId(questions[Math.min(count - 1, Math.max(0, index + by))].tool_use_id);

  return (
    <section className={cx(styles.card, expanded && styles.expanded)} aria-label={count === 1 ? "Question waiting" : `${count} questions waiting`}>
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={expanded}
        aria-controls={expanded ? bodyId : undefined}
        onClick={() => setExpanded(!expanded)}
      >
        <span className={styles.lamp} aria-hidden>
          ?
        </span>
        <span className={styles.prompt}>{prompt}</span>
        {count > 1 ? <span className={styles.count}>{count}</span> : null}
        <span className={styles.chevron}>
          <Icon name={expanded ? "chevron-down" : "chevron-up"} size={18} />
        </span>
      </button>
      {expanded ? (
        <div id={bodyId} className={styles.body}>
          <div className={styles.meta}>
            <span>
              {agentName} asked <EventTime ts={current.event.ts} />
            </span>
            {count > 1 ? (
              <span className={styles.pager}>
                <IconButton label="Previous question" size="sm" disabled={index === 0} onClick={() => step(-1)}>
                  <Icon name="chevron-left" size={16} />
                </IconButton>
                <span>
                  {index + 1} / {count}
                </span>
                <IconButton label="Next question" size="sm" disabled={index === count - 1} onClick={() => step(1)}>
                  <Icon name="chevron-right" size={16} />
                </IconButton>
              </span>
            ) : null}
            <ActionChip
              className={styles.reveal}
              onClick={() => {
                setExpanded(false);
                onReveal(current.tool_use_id);
              }}
            >
              Show in transcript
            </ActionChip>
          </div>
          {parsed ? (
            <AskForm key={current.tool_use_id} event={current.event} questions={parsed} />
          ) : (
            <p className={styles.fallback}>{current.event.text}</p>
          )}
        </div>
      ) : null}
    </section>
  );
}
