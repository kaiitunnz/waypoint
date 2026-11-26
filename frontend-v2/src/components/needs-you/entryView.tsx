import type { ReactNode } from "react";

import { gateHeadline, gateView } from "@waypoint/web-core/board";
import { inboxBlockChips } from "@waypoint/web-core/inbox";
import { askRow, type QueueEntry } from "@waypoint/web-core/needsYou";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { PathLabel } from "@/components/session/cards/PathLabel";
import { Icon } from "@/components/ui/Icon";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";

import styles from "./entryView.module.css";

export interface EntryView {
  // The lamp or glyph of the source.
  lead: ReactNode;
  name: string;
  kind: string;
  headline: string;
  // The line under the headline; null when there is nothing to add.
  detail: ReactNode;
  // Waiting on the human, or an inbox item not yet read.
  strong: boolean;
  // Nothing left for the human to do here.
  quiet: boolean;
  // The headline and detail wrap rather than being cut: a row answers from them.
  whole?: boolean;
}

export function entryView(entry: QueueEntry, agentName: (backend: string) => string): EntryView {
  if (entry.source === "sessions") {
    const { ask } = entry;
    const request = askRow(ask);
    return {
      lead: <StatusLamp state="needs" size="sm" flat />,
      name: ask.session_title,
      kind: agentName(ask.session_backend),
      headline: ask.kind === "question" && ask.question.questions.length === 1 ? ask.question.questions[0].question : ask.title,
      detail: request.path ? (
        <PathLabel {...splitPath(request.path)} full={request.path} />
      ) : request.text ? (
        <span className={cx(request.mono && "mono")}>{request.text}</span>
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
      lead: (
        <span className={cx(styles.glyph, resolved && styles.done)} role="img" aria-label={resolved ? "Resolved" : "Open"}>
          <Icon name={resolved ? "check" : "mail"} size={13} strokeWidth={2} />
        </span>
      ),
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
    lead: (
      <span className={cx(styles.glyph, answered && styles.done)} aria-hidden="true">
        <Icon name="board" size={12} strokeWidth={2} />
      </span>
    ),
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
