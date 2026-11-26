"use client";

import { useState, type KeyboardEvent } from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { askNeedsDetail } from "@waypoint/web-core/needsYou";
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
  const [picked, setPicked] = useState<string | null>(null);
  const prompts = ask.question.questions;
  const prompt = prompts[0];
  const disabled = busy || ask.availability !== "actionable";

  const answer = (label: string) => {
    setPicked(label);
    const tapped = tapOption(prompts, emptyPicks(prompts), 0, label);
    void send((host, token, retry) => answerQuestion(host, token, ask, tapped.picks, retry));
  };

  return (
    <>
      <OptionChips
        compact
        options={prompt.options}
        selected={picked && busy ? [picked] : []}
        multi={false}
        disabled={disabled}
        onOption={answer}
      />
      <AskStatus availability={ask.availability} phase={phase} onRetry={picked ? () => answer(picked) : undefined} />
    </>
  );
}

function ApprovalActions({ ask }: { ask: PendingApprovalAsk }) {
  const { actions, supportsNote } = decisionView(ask);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [last, setLast] = useState<string | null>(null);
  const { phase, send, busy } = useAskResponder(ask.id);
  const disabled = busy || ask.availability !== "actionable";

  const decide = (decision: string) => {
    setLast(decision);
    void send((host, token, retry) => decideAsk(host, token, ask, decision, note, retry));
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
            busy={phase.state === "sending" && last === action.decision}
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
      <AskStatus availability={ask.availability} phase={phase} onRetry={last ? () => decide(last) : undefined} />
    </>
  );
}

// The answer a row can take itself; a long ask has none.
export function AskRowActions({ ask }: { ask: PendingAsk }) {
  if (askNeedsDetail(ask) || ask.kind === "plan_approval") return null;
  return (
    <div className={styles.actions}>
      {ask.kind === "question" ? <QuestionActions ask={ask} /> : <ApprovalActions ask={ask} />}
    </div>
  );
}
