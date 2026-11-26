import type { ReactNode } from "react";

import { formatSessionTime } from "@waypoint/web-core/sessionPresentation";
import { sessionHref } from "@waypoint/web-core/shellNav";
import type { PendingAsk } from "@waypoint/web-core/types";

import { StatusLamp } from "@/components/ui/StatusLamp";

import { SourceLine } from "./SourceLine";
import styles from "./AskCard.module.css";

interface AskCardProps {
  ask: PendingAsk;
  agent: string;
  now: number;
  // Whether the source name goes to the session. A card that opens the ask keeps
  // one target at its top.
  linked: boolean;
  children: ReactNode;
}

export function AskCard({ ask, agent, now, linked, children }: AskCardProps) {
  return (
    <section className={styles.card} aria-label={`${ask.session_title}: ${ask.title}`}>
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
