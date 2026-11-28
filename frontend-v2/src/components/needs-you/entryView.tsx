import type { ReactNode } from "react";

import { gateHeadline, gateView } from "@waypoint/web-core/board";
import { inboxBlockChips } from "@waypoint/web-core/inbox";
import { askRow, rowLine, type QueueEntry } from "@waypoint/web-core/needsYou";
import { askTitle } from "@waypoint/web-core/pendingAskActions";
import { plainTitle } from "@waypoint/web-core/session/summary";
import type { AskSession } from "@waypoint/web-core/session/wholeRequest";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { Lines } from "@/components/session/cards/CommandText";
import { PathLabel } from "@/components/session/cards/PathLabel";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { TitleText } from "@/components/ui/SummaryText";
import { cx } from "@/lib/cx";

import { SourceGlyph } from "./SourceGlyph";
import styles from "./entryView.module.css";

export interface EntryView {
  lead: ReactNode;
  name: string;
  kind: string;
  headline: ReactNode;
  // The headline for a control's accessible label.
  label: string;
  detail: ReactNode;
  // Waiting on the human, or an inbox item not yet read.
  strong: boolean;
  // Nothing left for the human to do here.
  quiet: boolean;
  // Wraps the headline and detail in full: a row answers from them.
  whole?: boolean;
}

export function entryView(
  entry: QueueEntry,
  agentName: (backend: string) => string,
  askSession: (sessionId: string) => AskSession,
): EntryView {
  if (entry.source === "sessions") {
    const { ask } = entry;
    const session = askSession(ask.session_id);
    const request = askRow(ask, session);
    const title =
      ask.kind === "question" && ask.question.questions.length === 1
        ? { text: ask.question.questions[0].question, marked: false }
        : askTitle(ask, session.cwd);
    const line = rowLine(request, title);
    return {
      lead: <StatusLamp state="needs" size="sm" flat />,
      name: ask.session_title,
      kind: agentName(ask.session_backend),
      headline: <TitleText title={title} />,
      label: plainTitle(title),
      detail: request.path ? (
        <PathLabel {...splitPath(request.path)} full={request.path} />
      ) : line !== null ? (
        // Each line of a wrapped command hangs under its own start.
        <span className={cx(request.mono && "mono")}>{request.mono && request.answers ? <Lines text={line} /> : line}</span>
      ) : null,
      strong: true,
      quiet: false,
      whole: request.answers,
    };
  }
  if (entry.source === "inbox") {
    const { item } = entry;
    const resolved = item.status === "resolved";
    const chips = inboxBlockChips(item);
    return {
      lead: <SourceGlyph icon={resolved ? "check" : "mail"} done={resolved} label={resolved ? "Resolved" : "Open"} />,
      name: item.from_label ?? "unknown",
      kind: "Inbox",
      headline: item.subject,
      label: item.subject,
      detail:
        chips.length > 0 ? (
          <span className={styles.chips}>
            {chips.map((chip) => (
              <span key={chip.key} className={cx(chip.pending && !resolved && styles.pending)}>
                {chip.label}
              </span>
            ))}
          </span>
        ) : null,
      strong: !item.read_at,
      quiet: resolved,
    };
  }
  const { gate, answered } = entry;
  return {
    lead: <SourceGlyph icon="board" done={answered} />,
    name: "Board",
    kind: gateView(gate.ticket).gate,
    headline: gateHeadline(gate.ticket),
    label: gateHeadline(gate.ticket),
    detail: (
      <span className={styles.gate}>
        {answered ? <span className={styles.answered}>Answered</span> : null}
        <span>{gate.ticket.title}</span>
      </span>
    ),
    strong: !answered,
    quiet: answered,
  };
}
