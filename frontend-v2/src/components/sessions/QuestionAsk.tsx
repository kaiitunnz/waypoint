"use client";

import { useState } from "react";

import {
  buildAnswer,
  emptyPicks,
  isAnswerable,
  toggleOption,
  type PromptPick,
} from "@waypoint/web-core/pendingAskActions";
import type { PendingQuestionAsk } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { Modal, Sheet } from "@/components/ui/Dialog";
import { useDesktop } from "@/lib/layout";

import { AskCard } from "./AskCard";
import { AskStatus } from "./AskStatus";
import { PromptField } from "./PromptField";
import styles from "./Ask.module.css";
import { useAskResponder } from "./useAskResponder";

interface QuestionAskProps {
  ask: PendingQuestionAsk;
  agent: string;
  now: number;
}

// A question answered where it is listed. One single-select question sends on a
// tap; a multi-select question, an Other answer, or several questions take a
// Send button.
export function QuestionAsk({ ask, agent, now }: QuestionAskProps) {
  const desktop = useDesktop();
  const prompts = ask.question.questions;
  const single = prompts.length === 1 ? prompts[0] : null;
  const [picks, setPicks] = useState<PromptPick[]>(() => emptyPicks(prompts));
  const [open, setOpen] = useState(false);
  const { phase, send, busy } = useAskResponder(ask);
  const actionable = ask.availability === "actionable";
  const disabled = busy || !actionable;
  const answerable = isAnswerable(prompts, picks);

  const update = (index: number, change: (pick: PromptPick) => PromptPick) =>
    setPicks((current) => current.map((pick, i) => (i === index ? change(pick) : pick)));

  async function submit(next: PromptPick[] = picks) {
    if (!buildAnswer(prompts, next)) return;
    if (await send({ kind: "answer", picks: next })) setOpen(false);
  }

  function pickOption(index: number, label: string) {
    const prompt = prompts[index];
    // A lone single-select question has nothing left to ask once an option is chosen,
    // so a tap always sends it, including a second tap after a failed send.
    if (single && !prompt.multi_select) {
      const chosen = { labels: [label], other: "" };
      update(index, () => chosen);
      void submit([chosen]);
      return;
    }
    update(index, () => toggleOption(picks[index], label, prompt.multi_select));
  }

  const Surface = desktop ? Modal : Sheet;
  const headline = single ? single.question : ask.title;

  return (
    <AskCard ask={ask} agent={agent} now={now}>
      <h3 className={styles.headline}>{headline}</h3>
      {single ? (
        <div className={styles.body}>
          <PromptField
            prompt={single}
            pick={picks[0]}
            disabled={disabled}
            limit={desktop ? undefined : 2}
            onOption={(label) => pickOption(0, label)}
            onCustom={(text) => update(0, (pick) => ({ ...pick, other: text }))}
            onSubmitCustom={() => void submit()}
          />
          {single.multi_select || picks[0].other.trim() ? (
            <div className={styles.actions}>
              <Button variant="primary" size="sm" disabled={disabled || !answerable} onClick={() => void submit()}>
                Send
              </Button>
            </div>
          ) : null}
        </div>
      ) : (
        <div className={styles.actions}>
          <Button variant="primary" size="sm" disabled={disabled} onClick={() => setOpen(true)}>
            Answer
          </Button>
          <span className={styles.meta}>{prompts.length} questions</span>
        </div>
      )}
      <AskStatus availability={ask.availability} phase={phase} />
      {single ? null : (
        <Surface
          open={open}
          onClose={() => setOpen(false)}
          title={ask.title}
          subtitle={ask.session_title}
          footer={
            <Button variant="primary" busy={phase.state === "sending"} disabled={disabled || !answerable} onClick={() => void submit()}>
              Send
            </Button>
          }
        >
          <div className={styles.prompts}>
            {prompts.map((prompt, index) => (
              <PromptField
                key={prompt.question}
                prompt={prompt}
                pick={picks[index]}
                disabled={disabled}
                showQuestion
                onOption={(label) => update(index, (pick) => toggleOption(pick, label, prompt.multi_select))}
                onCustom={(text) => update(index, (pick) => ({ ...pick, other: text }))}
              />
            ))}
            <AskStatus availability={ask.availability} phase={phase} />
          </div>
        </Surface>
      )}
    </AskCard>
  );
}
