"use client";

import { needsHref } from "@waypoint/web-core/needsYou";
import { askBodyLine, askPreview, askTitle, decisionView, inlineCommand, planPreview } from "@waypoint/web-core/pendingAskActions";
import type { PendingApprovalAsk, PendingPlanAsk } from "@waypoint/web-core/types";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { CommandText } from "@/components/session/cards/CommandText";
import { PathLabel } from "@/components/session/cards/PathLabel";
import { Button } from "@/components/ui/Button";
import { TextInput } from "@/components/ui/Field";
import { TitleText } from "@/components/ui/SummaryText";

import { DecisionButtons, useApprovalReply } from "./ApprovalReply";
import { AskCard } from "./AskCard";
import { OpenHeadline, OpenLink } from "./AskOpen";
import { AskStatus } from "./AskStatus";
import styles from "./Ask.module.css";
import { useAskSessions } from "./useAskSessions";

interface DecisionAskProps {
  ask: PendingApprovalAsk | PendingPlanAsk;
  agent: string;
  now: number;
  openHref?: string;
}

// A tool approval or a plan approval.
export function DecisionAsk({ ask, agent, now, openHref }: DecisionAskProps) {
  const { planBody } = decisionView(ask);
  const reply = useApprovalReply(ask);
  const askSession = useAskSessions();
  const { cwd, fromScreen } = askSession(ask.session_id);
  const preview = askPreview(ask, cwd);
  // Only a command is decided here, and then the card shows it whole.
  const inline = inlineCommand(ask, fromScreen) !== null;
  const status = <AskStatus availability={ask.availability} phase={reply.phase} />;
  const title = <TitleText title={askTitle(ask, cwd)} />;
  const headline = openHref ? <OpenHeadline href={openHref}>{title}</OpenHeadline> : <h3 className={styles.headline}>{title}</h3>;
  const command =
    preview?.kind === "command" ? (
      <div className={styles.command}>
        <CommandText text={preview.text} whole={inline} />
      </div>
    ) : null;
  const path =
    preview?.kind === "path" ? (
      <div className={styles.path}>
        <PathLabel {...splitPath(preview.path)} full={preview.path} />
      </div>
    ) : null;
  const body = askBodyLine(ask, cwd);
  const summary =
    planBody !== null ? (
      <p className={styles.clamp}>{planPreview(planBody)}</p>
    ) : body && !command ? (
      <p className={styles.clamp}>{body}</p>
    ) : null;

  if (!inline) {
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
      {reply.noteOpen ? (
        <div className={styles.note}>
          <TextInput autoFocus {...reply.noteProps} />
        </div>
      ) : null}
      <div className={styles.actions}>
        <DecisionButtons reply={reply} />
        {reply.offersNote ? (
          <Button variant="text" size="sm" className={styles.end} onClick={reply.openNote}>
            Add note
          </Button>
        ) : null}
      </div>
      {status}
    </AskCard>
  );
}
