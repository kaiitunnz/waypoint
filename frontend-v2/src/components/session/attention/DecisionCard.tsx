"use client";

import { useState, type KeyboardEvent, type ReactNode } from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { retryWhileNotReady } from "@waypoint/web-core/pendingAskActions";
import type { ApprovalAction } from "@waypoint/web-core/session/approvals";
import type { PendingAsk } from "@waypoint/web-core/types";

import { AskStatus } from "@/components/sessions/AskStatus";
import { useAskResponder } from "@/components/sessions/useAskResponder";
import { Button } from "@/components/ui/Button";
import { cx } from "@/lib/cx";
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
  // Whether the agent can take a reply now.
  availability?: PendingAsk["availability"];
  // Shares the reply's phase with the ask's other surfaces.
  lockId?: string;
  // On its own screen: the body takes its full height.
  roomy?: boolean;
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
  availability = "actionable",
  lockId,
  roomy,
}: DecisionCardProps<Id>) {
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const { phase, send, busy: pending } = useAskResponder(lockId);
  const sending = (id: Id) => phase.state === "sending" && phase.action === id;
  const busy = pending || availability !== "actionable";

  const run = (id: Id) => {
    if (busy) return;
    const text = note.trim() || undefined;
    void send((_host, _token, retry) => retryWhileNotReady(() => decide(id, text), retry), id);
  };
  const primary = actions.find((action) => action.primary) ?? actions[0];

  const onNoteKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    if (primary) run(primary.id);
  };

  return (
    <section className={cx(styles.card, roomy && styles.roomy)} aria-label="Needs your decision">
      <div className={styles.head}>
        <span className={styles.lamp}>
          <StatusLamp state="needs" size="sm" flat />
        </span>
        <h3 className={styles.title}>{title}</h3>
        {pager?.(pending)}
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
            busy={sending(action.id)}
            onClick={() => run(action.id)}
          >
            {sending(action.id) && action.loadingLabel ? action.loadingLabel : action.label}
          </Button>
        ))}
        {supportsNote && !noteOpen ? (
          <Button variant="text" size="sm" className={styles.end} disabled={busy} onClick={() => setNoteOpen(true)}>
            Add note
          </Button>
        ) : null}
      </div>
      <AskStatus availability={availability} phase={phase} />
    </section>
  );
}
