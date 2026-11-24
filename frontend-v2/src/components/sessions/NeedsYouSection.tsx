"use client";

import Link from "next/link";
import { useState } from "react";

import type { PendingAsk } from "@waypoint/web-core/types";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { SectionHeading } from "@/components/ui/SectionHeading";

import { DecisionAsk } from "./DecisionAsk";
import { QuestionAsk } from "./QuestionAsk";
import styles from "./NeedsYouSection.module.css";

// The cards one screen shows before the rest fold behind "Show more".
const MOBILE_LIMIT = 3;

interface NeedsYouSectionProps {
  asks: PendingAsk[];
  // Everything waiting on the human, inbox items included.
  waiting: number;
  desktop: boolean;
  agentLabel: (ask: PendingAsk) => string;
  now: number;
}

export function NeedsYouSection({ asks, waiting, desktop, agentLabel, now }: NeedsYouSectionProps) {
  const [all, setAll] = useState(false);
  // Inbox items have no card here; one row opens the inbox until the queue lists them.
  const inbox = Math.max(0, waiting - asks.length);
  if (asks.length === 0 && inbox === 0) return null;
  const folded = !desktop && !all && asks.length > MOBILE_LIMIT;
  const shown = folded ? asks.slice(0, MOBILE_LIMIT) : asks;

  return (
    <section className={styles.section}>
      <SectionHeading title="Needs you" count={asks.length + inbox} dense={!desktop} />
      <div className={styles.cards}>
        {shown.map((ask) => {
          const agent = agentLabel(ask);
          return ask.kind === "question" ? (
            <QuestionAsk key={ask.id} ask={ask} agent={agent} now={now} />
          ) : (
            <DecisionAsk key={ask.id} ask={ask} agent={agent} now={now} />
          );
        })}
      </div>
      {folded ? (
        <div className={styles.more}>
          <Button variant="text" size="sm" onClick={() => setAll(true)}>
            Show {asks.length - MOBILE_LIMIT} more
          </Button>
        </div>
      ) : null}
      {inbox > 0 ? (
        <div className={styles.inboxWrap}>
          <Link href="/inbox" className={styles.inbox} aria-label={`${inbox} in inbox`}>
            <Badge count={inbox} />
            <span aria-hidden="true">in inbox</span>
            <Icon name="chevron-right" size={16} strokeWidth={2} />
          </Link>
        </div>
      ) : null}
    </section>
  );
}
