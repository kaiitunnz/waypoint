"use client";

import { useState, type KeyboardEvent } from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { askNeedsDetail } from "@waypoint/web-core/needsYou";
import {
  decideAsk,
  decisionView,
  planPreview,
  type DecisionAction,
  type DecisionKind,
} from "@waypoint/web-core/pendingAskActions";
import type { PendingApprovalAsk, PendingPlanAsk } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { TextInput } from "@/components/ui/Field";

import { AskCard } from "./AskCard";
import { OpenHeadline, OpenLink } from "./AskOpen";
import { AskStatus } from "./AskStatus";
import styles from "./Ask.module.css";
import { useAskResponder } from "./useAskResponder";

interface DecisionAskProps {
  ask: PendingApprovalAsk | PendingPlanAsk;
  agent: string;
  now: number;
  // Where the ask opens; a long one is decided there instead of on the card.
  openHref?: string;
}

const VARIANT = { approve: "primary", session: "text", decline: "danger" } as const satisfies Record<
  DecisionKind,
  string
>;

// A tool approval or a plan approval.
export function DecisionAsk({ ask, agent, now, openHref }: DecisionAskProps) {
  const { actions, supportsNote, preview, planBody } = decisionView(ask);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [last, setLast] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  const { phase, send, busy } = useAskResponder(ask.id);
  const disabled = busy || ask.availability !== "actionable";
  const long = openHref !== undefined && askNeedsDetail(ask);

  const decide = async (decision: string) => {
    setLast(decision);
    if (await send((host, token, retry) => decideAsk(host, token, ask, decision, note, retry))) setReading(false);
  };
  const onNoteKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    const primary = actions.find((action) => action.kind === "approve");
    if (primary && !disabled) void decide(primary.decision);
  };
  const button = (action: DecisionAction, size: "sm" | "md" = "sm") => (
    <Button
      key={action.decision}
      variant={VARIANT[action.kind]}
      size={size}
      disabled={disabled}
      busy={phase.state === "sending"}
      onClick={() => void decide(action.decision)}
    >
      {action.label}
    </Button>
  );
  const status = (
    <AskStatus availability={ask.availability} phase={phase} onRetry={last ? () => void decide(last) : undefined} />
  );
  const headline = openHref ? <OpenHeadline href={openHref}>{ask.title}</OpenHeadline> : <h3 className={styles.headline}>{ask.title}</h3>;
  const command = preview?.kind === "command" ? <pre className={styles.command}>{preview.text}</pre> : null;
  const path = preview?.kind === "path" ? <p className={`${styles.path} mono`}>{preview.path}</p> : null;

  if (long) {
    return (
      <AskCard ask={ask} agent={agent} now={now}>
        {headline}
        <div className={styles.review}>
          <div>
            {command}
            {path}
            {planBody !== null ? <p className={styles.clamp}>{planPreview(planBody)}</p> : null}
          </div>
          <OpenLink href={openHref} label="Review" />
        </div>
        {status}
      </AskCard>
    );
  }

  return (
    <AskCard ask={ask} agent={agent} now={now}>
      {headline}
      {command}
      {path}
      {planBody !== null ? (
        <p className={styles.clamp}>{planPreview(planBody)}</p>
      ) : ask.body && preview?.kind !== "command" ? (
        <p className={styles.clamp}>{ask.body}</p>
      ) : null}
      {noteOpen ? (
        <div className={styles.note}>
          <TextInput
            autoFocus
            value={note}
            disabled={disabled}
            aria-label="Note"
            placeholder="Note"
            onChange={(event) => setNote(event.target.value)}
            onKeyDown={onNoteKey}
          />
        </div>
      ) : null}
      <div className={styles.actions}>
        {actions.map((action) => button(action))}
        {planBody !== null ? (
          <Button variant="text" size="sm" onClick={() => setReading(true)}>
            Read plan
          </Button>
        ) : null}
        {supportsNote && !noteOpen ? (
          <Button variant="text" size="sm" className={styles.end} onClick={() => setNoteOpen(true)}>
            Add note
          </Button>
        ) : null}
      </div>
      {status}
      {planBody !== null ? (
        <Dialog
          open={reading}
          onClose={() => setReading(false)}
          title={ask.title}
          subtitle={ask.session_title}
          footer={<>{actions.map((action) => button(action, "md"))}</>}
        >
          <p className={styles.plan}>{planBody}</p>
        </Dialog>
      ) : null}
    </AskCard>
  );
}
