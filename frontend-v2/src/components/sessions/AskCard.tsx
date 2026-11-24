import Link from "next/link";
import type { ReactNode } from "react";

import { formatSessionTime } from "@waypoint/web-core/sessionPresentation";
import type { PendingAsk } from "@waypoint/web-core/types";

import { StatusLamp } from "@/components/ui/StatusLamp";

import styles from "./AskCard.module.css";

interface AskCardProps {
  ask: PendingAsk;
  agent: string;
  now: number;
  children: ReactNode;
}

// The attention tier: only an item waiting on the human takes this surface.
export function AskCard({ ask, agent, now, children }: AskCardProps) {
  return (
    <section className={styles.card} aria-label={`${ask.session_title}: ${ask.title}`}>
      <div className={styles.source}>
        <StatusLamp state="needs" size="sm" flat />
        <Link href={`/session/${encodeURIComponent(ask.session_id)}`} className={styles.name}>
          {ask.session_title}
        </Link>
        <span className={styles.agent}>{agent}</span>
        <time className={styles.time} dateTime={ask.created_at}>
          {formatSessionTime(ask.created_at, now)}
        </time>
      </div>
      {children}
    </section>
  );
}
