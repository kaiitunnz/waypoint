"use client";

import { askRow, needsHref } from "@waypoint/web-core/needsYou";
import { answerQuestion, emptyPicks, tapOption } from "@waypoint/web-core/pendingAskActions";
import type { PendingAsk, PendingApprovalAsk, PendingQuestionAsk } from "@waypoint/web-core/types";

import { DecisionButtons, useApprovalReply } from "@/components/sessions/ApprovalReply";
import { OpenLink } from "@/components/sessions/AskOpen";
import { AskStatus } from "@/components/sessions/AskStatus";
import { OptionChips } from "@/components/sessions/OptionChips";
import { useAskResponder } from "@/components/sessions/useAskResponder";
import { useAskSessions } from "@/components/sessions/useAskSessions";
import { TextInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";

import styles from "./AskRowActions.module.css";

function QuestionActions({ ask }: { ask: PendingQuestionAsk }) {
  const { phase, send, busy } = useAskResponder(ask.id);
  const prompts = ask.question.questions;
  const prompt = prompts[0];
  const disabled = busy || ask.availability !== "actionable";
  const picked = phase.state === "sending" || phase.state === "sent" ? phase.action : undefined;

  const answer = (label: string) => {
    const tapped = tapOption(prompts, emptyPicks(prompts), 0, label);
    void send((host, token, retry) => answerQuestion(host, token, ask, tapped.picks, retry), label);
  };

  return (
    <>
      <OptionChips
        compact
        options={prompt.options}
        selected={picked ? [picked] : []}
        multi={false}
        disabled={disabled}
        onOption={answer}
      />
      <AskStatus availability={ask.availability} phase={phase} />
    </>
  );
}

function ApprovalActions({ ask }: { ask: PendingApprovalAsk }) {
  const reply = useApprovalReply(ask);

  return (
    <>
      {reply.noteOpen ? <TextInput autoFocus className={styles.note} {...reply.noteProps} /> : null}
      <div className={styles.buttons}>
        <DecisionButtons reply={reply} />
        {reply.offersNote ? (
          <IconButton label="Add note" size="sm" className={styles.noteButton} disabled={reply.disabled} onClick={reply.openNote}>
            <Icon name="pencil" size={16} />
          </IconButton>
        ) : null}
      </div>
      <AskStatus availability={ask.availability} phase={reply.phase} />
    </>
  );
}

// A row takes the reply to a request it shows whole. Another approval offers Review.
export function AskRowActions({ ask }: { ask: PendingAsk }) {
  const askSession = useAskSessions();
  const { answers } = askRow(ask, askSession(ask.session_id));
  if (ask.kind === "question") {
    return answers ? (
      <div className={styles.actions}>
        <QuestionActions ask={ask} />
      </div>
    ) : null;
  }
  return (
    <div className={styles.actions}>
      {answers && ask.kind === "approval" ? (
        <ApprovalActions ask={ask} />
      ) : (
        <div className={styles.buttons}>
          <OpenLink href={needsHref(ask.id)} label="Review" replace />
        </div>
      )}
    </div>
  );
}
