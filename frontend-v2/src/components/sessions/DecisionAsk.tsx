"use client";

import { useState } from "react";

import {
  approvalActions,
  approvalPreview,
  planActions,
  planPreview,
  type AskDecision,
  type DecisionAction,
} from "@waypoint/web-core/pendingAskActions";
import type { PendingApprovalAsk, PendingPlanAsk } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { Modal, Sheet } from "@/components/ui/Dialog";
import { TextInput } from "@/components/ui/Field";
import { useDesktop } from "@/lib/layout";

import { AskCard } from "./AskCard";
import { AskStatus } from "./AskStatus";
import styles from "./Ask.module.css";
import { useAskResponder } from "./useAskResponder";

interface DecisionAskProps {
  ask: PendingApprovalAsk | PendingPlanAsk;
  agent: string;
  now: number;
}

const VARIANT = { primary: "primary", quiet: "text", danger: "danger" } as const;

// A tool approval or a plan approval: approve, approve for the session when the
// agent allows it, decline, and a note when the agent takes one.
export function DecisionAsk({ ask, agent, now }: DecisionAskProps) {
  const desktop = useDesktop();
  const approval = ask.kind === "approval" ? ask.approval : null;
  const plan = ask.kind === "plan_approval" ? ask.plan : null;
  const actions = approval ? approvalActions(approval) : planActions(plan!);
  const supportsNote = (approval ?? plan!).supports_note;
  const preview = approval ? approvalPreview(approval) : null;
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [reading, setReading] = useState(false);
  const { phase, send, busy } = useAskResponder(ask);
  const disabled = busy || ask.availability !== "actionable";
  const Surface = desktop ? Modal : Sheet;

  const decide = async (decision: AskDecision) => {
    if (await send({ kind: "decision", decision, note })) setReading(false);
  };
  const button = (action: DecisionAction, size: "sm" | "md" = "sm") => (
    <Button
      key={action.decision}
      variant={VARIANT[action.tone]}
      size={size}
      disabled={disabled}
      busy={phase.state === "sending"}
      onClick={() => void decide(action.decision)}
    >
      {action.label}
    </Button>
  );

  return (
    <AskCard ask={ask} agent={agent} now={now}>
      <h3 className={styles.headline}>{ask.title}</h3>
      {preview?.kind === "command" ? <pre className={styles.command}>{preview.text}</pre> : null}
      {preview?.kind === "path" ? <p className={`${styles.path} mono`}>{preview.path}</p> : null}
      {plan ? <p className={styles.clamp}>{planPreview(plan.text)}</p> : ask.body && preview?.kind !== "command" ? (
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
          />
        </div>
      ) : null}
      <div className={styles.actions}>
        {actions.map((action) => button(action))}
        {plan ? (
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
      <AskStatus availability={ask.availability} phase={phase} />
      {plan ? (
        <Surface
          open={reading}
          onClose={() => setReading(false)}
          title={ask.title}
          subtitle={ask.session_title}
          footer={<>{actions.map((action) => button(action, "md"))}</>}
        >
          <p className={styles.plan}>{plan.text}</p>
        </Surface>
      ) : null}
    </AskCard>
  );
}
