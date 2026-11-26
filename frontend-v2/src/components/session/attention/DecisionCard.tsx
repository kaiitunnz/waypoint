"use client";

import { useState, type KeyboardEvent, type ReactNode } from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { retryWhileNotReady } from "@waypoint/web-core/pendingAskActions";
import type { ApprovalAction } from "@waypoint/web-core/session/approvals";

import { useAskResponder } from "@/components/sessions/useAskResponder";
import { ActionChip } from "@/components/ui/ActionChip";
import { Button } from "@/components/ui/Button";
import { TextArea } from "@/components/ui/Field";

import { CopyButton } from "../cards/CopyButton";
import styles from "./DecisionCard.module.css";

interface DecisionCardProps<Id extends string> {
  title: ReactNode;
  // Beside the title: a pager or a time.
  meta?: ReactNode;
  copyText: string;
  copyLabel: string;
  actions: ApprovalAction<Id>[];
  supportsNote: boolean;
  // Sends one decision; a not-ready agent is retried.
  decide: (id: Id, note: string | undefined) => Promise<void>;
  children: ReactNode;
}

// A decision the human owes the agent: a body, then Approve and the other
// decisions, with an optional note. Every action locks while one is in flight.
export function DecisionCard<Id extends string>({
  title,
  meta,
  copyText,
  copyLabel,
  actions,
  supportsNote,
  decide,
  children,
}: DecisionCardProps<Id>) {
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [last, setLast] = useState<Id | null>(null);
  const { phase, send, busy } = useAskResponder();

  const run = (id: Id) => {
    if (busy) return;
    setLast(id);
    const text = note.trim() || undefined;
    void send((_host, _token, retry) => retryWhileNotReady(() => decide(id, text), retry));
  };
  const primary = actions.find((action) => action.primary) ?? actions[0];

  const onNoteKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    if (primary) run(primary.id);
  };

  return (
    <section className={styles.card} aria-label="Needs your decision">
      <div className={styles.head}>
        <span className={styles.lamp} aria-hidden />
        <h3 className={styles.title}>{title}</h3>
        {meta}
        <CopyButton text={copyText} label={copyLabel} />
      </div>
      <div className={styles.body}>{children}</div>
      {supportsNote && noteOpen ? (
        <TextArea
          autoFocus
          rows={2}
          value={note}
          disabled={busy}
          aria-label="Note"
          placeholder="Note"
          onChange={(event) => setNote(event.target.value)}
          onKeyDown={onNoteKey}
        />
      ) : null}
      <div className={styles.actions}>
        {actions.map((action) => (
          <Button
            key={action.id}
            variant={action.primary ? "primary" : "text"}
            size="sm"
            disabled={busy}
            busy={phase.state === "sending" && last === action.id}
            onClick={() => run(action.id)}
          >
            {phase.state === "sending" && last === action.id && action.loadingLabel ? action.loadingLabel : action.label}
          </Button>
        ))}
        {supportsNote && !noteOpen ? (
          <Button variant="text" size="sm" className={styles.end} disabled={busy} onClick={() => setNoteOpen(true)}>
            Add note
          </Button>
        ) : null}
      </div>
      {phase.state === "sending" && phase.retrying ? <p className={styles.status}>Waiting for the agent…</p> : null}
      {phase.state === "error" ? (
        <div className={styles.failure} role="alert">
          <p>{phase.message}</p>
          {last ? (
            <ActionChip icon="refresh" onClick={() => run(last)}>
              Retry
            </ActionChip>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
