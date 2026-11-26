"use client";

import { useState, type KeyboardEvent } from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { needsHref } from "@waypoint/web-core/needsYou";
import {
  decideAsk,
  decidesInline,
  decisionView,
  planPreview,
  type DecisionKind,
} from "@waypoint/web-core/pendingAskActions";
import type { PendingApprovalAsk, PendingPlanAsk } from "@waypoint/web-core/types";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { CommandText } from "@/components/session/cards/CommandText";
import { PathLabel } from "@/components/session/cards/PathLabel";
import { Button } from "@/components/ui/Button";
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
  // Set where the headline opens the ask, as in the queue.
  openHref?: string;
}

const VARIANT = { approve: "primary", session: "text", decline: "danger" } as const satisfies Record<
  DecisionKind,
  string
>;

// A tool approval or a plan approval. One whose request the card cannot show is
// decided in its detail.
export function DecisionAsk({ ask, agent, now, openHref }: DecisionAskProps) {
  const { actions, supportsNote, preview, planBody } = decisionView(ask);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const { phase, send, busy, dismiss } = useAskResponder(ask.id);
  const disabled = busy || ask.availability !== "actionable";

  const decide = (decision: string) =>
    void send((host, token, retry) => decideAsk(host, token, ask, decision, note, retry), decision);
  const onNoteKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    const primary = actions.find((action) => action.kind === "approve");
    if (primary && !disabled) decide(primary.decision);
  };
  const status = <AskStatus availability={ask.availability} phase={phase} />;
  const headline = openHref ? <OpenHeadline href={openHref}>{ask.title}</OpenHeadline> : <h3 className={styles.headline}>{ask.title}</h3>;
  const command =
    preview?.kind === "command" ? (
      <div className={styles.command}>
        <CommandText text={preview.text} />
      </div>
    ) : null;
  const path =
    preview?.kind === "path" ? (
      <div className={styles.path}>
        <PathLabel {...splitPath(preview.path)} full={preview.path} />
      </div>
    ) : null;
  const summary =
    planBody !== null ? (
      <p className={styles.clamp}>{planPreview(planBody)}</p>
    ) : ask.body && !command ? (
      <p className={styles.clamp}>{ask.body}</p>
    ) : null;

  if (!decidesInline(ask)) {
    return (
      <AskCard ask={ask} agent={agent} now={now} linked={openHref === undefined}>
        {headline}
        <div className={styles.review}>
          <div>
            {command}
            {path}
            {summary}
          </div>
          <OpenLink href={openHref ?? needsHref(ask.id)} label="Review" />
        </div>
        {status}
      </AskCard>
    );
  }

  return (
    <AskCard ask={ask} agent={agent} now={now} linked={openHref === undefined}>
      {headline}
      {command}
      {path}
      {summary}
      {noteOpen ? (
        <div className={styles.note}>
          <TextInput
            autoFocus
            value={note}
            disabled={disabled}
            aria-label="Note"
            placeholder="Note"
            onChange={(event) => {
              setNote(event.target.value);
              dismiss();
            }}
            onKeyDown={onNoteKey}
          />
        </div>
      ) : null}
      <div className={styles.actions}>
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
          <Button variant="text" size="sm" className={styles.end} onClick={() => setNoteOpen(true)}>
            Add note
          </Button>
        ) : null}
      </div>
      {status}
    </AskCard>
  );
}
