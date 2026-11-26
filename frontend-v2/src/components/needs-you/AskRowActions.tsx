"use client";

import { useState, type KeyboardEvent } from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { askRow } from "@waypoint/web-core/needsYou";
import {
  answerQuestion,
  decideAsk,
  decisionView,
  emptyPicks,
  tapOption,
  type DecisionKind,
} from "@waypoint/web-core/pendingAskActions";
import type { PendingAsk, PendingApprovalAsk, PendingQuestionAsk } from "@waypoint/web-core/types";

import { AskStatus } from "@/components/sessions/AskStatus";
import { OptionChips } from "@/components/sessions/OptionChips";
import { useAskResponder } from "@/components/sessions/useAskResponder";
import { Button } from "@/components/ui/Button";
import { TextInput } from "@/components/ui/Field";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";

import styles from "./AskRowActions.module.css";

const VARIANT = { approve: "primary", session: "text", decline: "danger" } as const satisfies Record<
  DecisionKind,
  string
>;

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
  const { actions, supportsNote } = decisionView(ask);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const { phase, send, busy } = useAskResponder(ask.id);
  const disabled = busy || ask.availability !== "actionable";

  const decide = (decision: string) => {
    void send((host, token, retry) => decideAsk(host, token, ask, decision, note, retry), decision);
  };
  const onNoteKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    if (!disabled) decide("accept");
  };

  return (
    <>
      {noteOpen ? (
        <TextInput
          autoFocus
          className={styles.note}
          value={note}
          disabled={disabled}
          aria-label="Note"
          placeholder="Note"
          onChange={(event) => setNote(event.target.value)}
          onKeyDown={onNoteKey}
        />
      ) : null}
      <div className={styles.buttons}>
        {actions.map((action) => (
          <Button
            key={action.decision}
            variant={VARIANT[action.kind]}
            size="sm"
            disabled={disabled}
            busy={phase.state === "sending" && phase.action === action.decision}
            onClick={() => decide(action.decision)}
          >
            {action.label}
          </Button>
        ))}
        {supportsNote && !noteOpen ? (
          <IconButton label="Add note" size="sm" className={styles.noteButton} disabled={disabled} onClick={() => setNoteOpen(true)}>
            <Icon name="pencil" size={16} />
          </IconButton>
        ) : null}
      </div>
      <AskStatus availability={ask.availability} phase={phase} />
    </>
  );
}

// The answer a row can take itself; one it cannot show whole has none.
export function AskRowActions({ ask }: { ask: PendingAsk }) {
  if (!askRow(ask).answers || ask.kind === "plan_approval") return null;
  return (
    <div className={styles.actions}>
      {ask.kind === "question" ? <QuestionActions ask={ask} /> : <ApprovalActions ask={ask} />}
    </div>
  );
}
