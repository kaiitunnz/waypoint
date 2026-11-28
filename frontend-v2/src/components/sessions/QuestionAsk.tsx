"use client";

import { useState } from "react";

import { askNeedsDetail, questionLine } from "@waypoint/web-core/needsYou";
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
import { usePhoneLayout } from "@/lib/layout";

import { AskCard } from "./AskCard";
import { OpenHeadline, OpenLink } from "./AskOpen";
import { AskStatus } from "./AskStatus";
import { PromptField } from "./PromptField";
import styles from "./Ask.module.css";
import { useAskResponder } from "./useAskResponder";

interface QuestionAskProps {
  ask: PendingQuestionAsk;
  agent: string;
  now: number;
  // Where the ask opens. With it there is no dialog: an ask `askNeedsDetail` flags is
  // answered there.
  openHref?: string;
}

export function QuestionAsk({ ask, agent, now, openHref }: QuestionAskProps) {
  const phone = usePhoneLayout();
  const prompts = ask.question.questions;
  const single = prompts.length === 1 ? prompts[0] : null;
  const [picks, setPicks] = useState<PromptPick[]>(() => emptyPicks(prompts));
  const [open, setOpen] = useState(false);
  const { phase, send, busy, dismiss } = useAskResponder(ask.id);
  const actionable = ask.availability === "actionable";
  const disabled = busy || !actionable;
  const answerable = isAnswerable(prompts, picks);
  const needsDetail = openHref !== undefined && askNeedsDetail(ask);

  const update = (index: number, change: (pick: PromptPick) => PromptPick) => {
    setPicks((current) => current.map((pick, i) => (i === index ? change(pick) : pick)));
    dismiss();
  };

  function submit(next: PromptPick[] = picks) {
    if (!isAnswerable(prompts, next)) return;
    void send(async (host, token, retry) => {
      await answerQuestion(host, token, ask, next, retry);
      // Inside the reply, so that a retry that goes through closes it too.
      setOpen(false);
    });
  }

  function pickOption(index: number, label: string) {
    const tapped = tapOption(prompts, picks, index, label);
    setPicks(tapped.picks);
    if (tapped.send) submit(tapped.picks);
    else dismiss();
  }

  const headline = single ? single.question : ask.title;
  const status = <AskStatus availability={ask.availability} phase={phase} />;

  return (
    <AskCard ask={ask} agent={agent} now={now} linked={openHref === undefined}>
      {openHref ? <OpenHeadline href={openHref}>{headline}</OpenHeadline> : <h3 className={styles.headline}>{headline}</h3>}
      {needsDetail ? (
        <div className={styles.review}>
          <p className={styles.meta}>{questionLine(ask)}</p>
          <OpenLink href={openHref} label="Answer" />
        </div>
      ) : single ? (
        <div className={styles.body}>
          <PromptField
            prompt={single}
            pick={picks[0]}
            disabled={disabled}
            limit={phone && !openHref ? 2 : undefined}
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
      {open ? null : status}
      {single || openHref ? null : (
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
                // With several prompts a tap never sends.
                onOption={(label) => pickOption(index, label)}
                onCustom={(text) => update(index, (pick) => ({ ...pick, other: text }))}
              />
            ))}
            {status}
          </div>
        </Dialog>
      )}
    </AskCard>
  );
}
