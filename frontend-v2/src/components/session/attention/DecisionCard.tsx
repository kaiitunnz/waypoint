"use client";

import { useState, type KeyboardEvent, type ReactNode } from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { retryWhileNotReady } from "@waypoint/web-core/pendingAskActions";
import type { ApprovalAction } from "@waypoint/web-core/session/approvals";

import { AskStatus } from "@/components/sessions/AskStatus";
import { useAskResponder } from "@/components/sessions/useAskResponder";
import { Button } from "@/components/ui/Button";
import { TextArea } from "@/components/ui/Field";
import { StatusLamp } from "@/components/ui/StatusLamp";

import { CopyButton } from "../cards/CopyButton";
import styles from "./DecisionCard.module.css";

interface DecisionCardProps<Id extends string> {
  title: ReactNode;
  // Locked while a decision is in flight or just sent, so it cannot go out twice.
  pager?: (locked: boolean) => ReactNode;
  copyText: string;
  copyLabel: string;
  actions: ApprovalAction<Id>[];
  supportsNote: boolean;
  // Sends one decision; a not-ready agent is retried.
  decide: (id: Id, note: string | undefined) => Promise<void>;
  children: ReactNode;
}

// Every action locks while one is in flight.
export function DecisionCard<Id extends string>({
  title,
  pager,
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
  const sending = phase.state === "sending";

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
        <span className={styles.lamp}>
          <StatusLamp state="needs" size="sm" flat />
        </span>
        <h3 className={styles.title}>{title}</h3>
        {pager?.(busy)}
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
            busy={sending && last === action.id}
            onClick={() => run(action.id)}
          >
            {sending && last === action.id && action.loadingLabel ? action.loadingLabel : action.label}
          </Button>
        ))}
        {supportsNote && !noteOpen ? (
          <Button variant="text" size="sm" className={styles.end} disabled={busy} onClick={() => setNoteOpen(true)}>
            Add note
          </Button>
        ) : null}
      </div>
      <AskStatus availability="actionable" phase={phase} onRetry={last ? () => run(last) : undefined} />
    </section>
  );
}
