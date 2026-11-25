"use client";

import { useState } from "react";

import {
  answerQuestion,
  emptyPicks,
  isAnswerable,
  showsSend,
  tapOption,
  type PromptPick,
} from "@waypoint/web-core/pendingAskActions";
import type { PendingQuestionAsk } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
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

export function QuestionAsk({ ask, agent, now }: QuestionAskProps) {
  const desktop = useDesktop();
  const prompts = ask.question.questions;
  const single = prompts.length === 1 ? prompts[0] : null;
  const [picks, setPicks] = useState<PromptPick[]>(() => emptyPicks(prompts));
  const [open, setOpen] = useState(false);
  const { phase, send, busy } = useAskResponder();
  const actionable = ask.availability === "actionable";
  const disabled = busy || !actionable;
  const answerable = isAnswerable(prompts, picks);

  const update = (index: number, change: (pick: PromptPick) => PromptPick) =>
    setPicks((current) => current.map((pick, i) => (i === index ? change(pick) : pick)));

  async function submit(next: PromptPick[] = picks) {
    if (!isAnswerable(prompts, next)) return;
    if (await send((host, token, retry) => answerQuestion(host, token, ask, next, retry))) setOpen(false);
  }

  function pickOption(index: number, label: string) {
    const tapped = tapOption(prompts, picks, index, label);
    setPicks(tapped.picks);
    if (tapped.send) void submit(tapped.picks);
  }

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
          {showsSend(single, picks[0]) ? (
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
      {open ? null : <AskStatus availability={ask.availability} phase={phase} />}
      {single ? null : (
        <Dialog
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
                onOption={(label) => setPicks(tapOption(prompts, picks, index, label).picks)}
                onCustom={(text) => update(index, (pick) => ({ ...pick, other: text }))}
              />
            ))}
            <AskStatus availability={ask.availability} phase={phase} />
          </div>
        </Dialog>
      )}
    </AskCard>
  );
}
