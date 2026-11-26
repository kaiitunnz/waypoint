"use client";

import { useEffect, useId, useRef, useState } from "react";

import { parseAskUserQuestion, pendingQuestionPrompt } from "@waypoint/web-core/session/askQuestion";
import { pageOfKey, stepPage } from "@waypoint/web-core/session/paging";
import type { PendingQuestion } from "@waypoint/web-core/types";

import { ActionChip } from "@/components/ui/ActionChip";
import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import { Disclosure } from "../cards/Disclosure";
import { EventTime } from "../cards/EventTime";
import { useClamp } from "../cards/useClamp";
import { useAskController, useSessionContext } from "../SessionContext";
import { AskForm } from "./AskForm";
import { Pager } from "./Pager";
import styles from "./QuestionDock.module.css";

interface QuestionDockProps {
  // Oldest first.
  questions: PendingQuestion[];
  onReveal: (toolUseId: string) => void;
}

const keyOf = (question: PendingQuestion) => question.tool_use_id;

// Collapsed to one line until the human opens it.
export function QuestionDock({ questions, onReveal }: QuestionDockProps) {
  const { agentName } = useSessionContext();
  const controller = useAskController();
  const [expanded, setExpanded] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  // Open, a long question is cut to a few lines with a disclosure.
  const [wholePrompt, setWholePrompt] = useState(false);
  const whole = expanded && wholePrompt;
  const { content, chip } = useClamp<HTMLSpanElement>(whole);
  const card = useRef<HTMLElement>(null);
  const bodyId = useId();

  // An Escape that a popover, a dialog or the composer handles is theirs.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const focus = document.activeElement;
      if (focus && focus !== document.body && !card.current?.contains(focus)) return;
      setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const { count, page, current } = pageOfKey(questions, openId, keyOf);
  if (!current) return null;
  const parsed = parseAskUserQuestion(current.event);
  const prompt = pendingQuestionPrompt(current, controller.draft(current.tool_use_id).activeIndex);

  return (
    <section ref={card} className={cx(styles.card, expanded && styles.expanded)} aria-label={count === 1 ? "Question waiting" : `${count} questions waiting`}>
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
        <span className={cx(styles.prompt, expanded && !whole && styles.clamped)}>
          <span ref={content} className={styles.promptText}>
            {prompt}
          </span>
        </span>
        {count > 1 ? <span className={styles.count}>{count}</span> : null}
        <span className={styles.chevron}>
          <Icon name={expanded ? "chevron-down" : "chevron-up"} size={18} />
        </span>
      </button>
      <div ref={chip} className={styles.more}>
        <Disclosure open={whole} label="Show whole question" onToggle={() => setWholePrompt(!whole)} />
      </div>
      {expanded ? (
        <div id={bodyId} className={styles.body}>
          <div className={styles.meta}>
            <span>
              {agentName} asked <EventTime ts={current.event.ts} />
            </span>
            {count > 1 ? (
              <Pager
                page={page}
                count={count}
                noun="question"
                onStep={(direction) => setOpenId(keyOf(questions[stepPage(page, direction, count)]))}
              />
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
            <AskForm key={current.tool_use_id} event={current.event} questions={parsed} headed />
          ) : (
            <p className={styles.fallback}>{current.event.text}</p>
          )}
        </div>
      ) : null}
    </section>
  );
}
