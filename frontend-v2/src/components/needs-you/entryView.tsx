import type { ReactNode } from "react";

import { gateHeadline, gateView } from "@waypoint/web-core/board";
import { inboxBlockChips } from "@waypoint/web-core/inbox";
import { askRow, type QueueEntry } from "@waypoint/web-core/needsYou";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { Lines } from "@/components/session/cards/CommandText";
import { PathLabel } from "@/components/session/cards/PathLabel";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";

import { SourceGlyph } from "./SourceGlyph";
import styles from "./entryView.module.css";

export interface EntryView {
  lead: ReactNode;
  name: string;
  kind: string;
  headline: string;
  detail: ReactNode;
  // Waiting on the human, or an inbox item not yet read.
  strong: boolean;
  // Nothing left for the human to do here.
  quiet: boolean;
  // Wraps the headline and detail in full: a row answers from them.
  whole?: boolean;
}

export function entryView(entry: QueueEntry, agentName: (backend: string) => string): EntryView {
  if (entry.source === "sessions") {
    const { ask } = entry;
    const request = askRow(ask);
    const headline = ask.kind === "question" && ask.question.questions.length === 1 ? ask.question.questions[0].question : ask.title;
    return {
      lead: <StatusLamp state="needs" size="sm" flat />,
      name: ask.session_title,
      kind: agentName(ask.session_backend),
      headline,
      detail: request.path ? (
        <PathLabel {...splitPath(request.path)} full={request.path} />
      ) : request.text && request.text !== headline ? (
        // Each line of a wrapped command hangs under its own start.
        <span className={cx(request.mono && "mono")}>
          {request.mono && request.answers ? <Lines text={request.text} /> : request.text}
        </span>
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
