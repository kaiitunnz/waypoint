import type { ReactNode } from "react";

import { askTitle } from "@waypoint/web-core/pendingAskActions";
import { plainTitle } from "@waypoint/web-core/session/summary";
import { formatSessionTime } from "@waypoint/web-core/sessionPresentation";
import { sessionHref } from "@waypoint/web-core/shellNav";
import type { PendingAsk } from "@waypoint/web-core/types";

import { StatusLamp } from "@/components/ui/StatusLamp";

import { SourceLine } from "./SourceLine";
import styles from "./AskCard.module.css";
import { useAskSessions } from "./useAskSessions";

interface AskCardProps {
  ask: PendingAsk;
  agent: string;
  now: number;
  // False when the headline opens the ask: a card has one target at its top.
  linked: boolean;
  children: ReactNode;
}

export function AskCard({ ask, agent, now, linked, children }: AskCardProps) {
  const title = askTitle(ask, useAskSessions()(ask.session_id).cwd);
  return (
    <section className={styles.card} aria-label={`${ask.session_title}: ${plainTitle(title)}`}>
      <SourceLine
        lead={<StatusLamp state="needs" size="sm" flat />}
        name={ask.session_title}
        href={linked ? sessionHref(ask.session_id) : undefined}
        kind={agent}
        time={<time dateTime={ask.created_at}>{formatSessionTime(ask.created_at, now)}</time>}
      />
      {children}
    </section>
  );
}
