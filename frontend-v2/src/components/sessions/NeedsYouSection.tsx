"use client";

import Link from "next/link";
import { useState } from "react";

import type { PendingAsk } from "@waypoint/web-core/types";

import { Badge } from "@/components/ui/Badge";
import { ActionChip } from "@/components/ui/ActionChip";
import { Icon } from "@/components/ui/Icon";
import { SectionHeading } from "@/components/ui/SectionHeading";
import { cx } from "@/lib/cx";

import { DecisionAsk } from "./DecisionAsk";
import { QuestionAsk } from "./QuestionAsk";
import styles from "./NeedsYouSection.module.css";

const PHONE_LIMIT = 3;

interface NeedsYouSectionProps {
  asks: PendingAsk[];
  // Open inbox items, which have no card here.
  inbox: number;
  phone: boolean;
  agentLabel: (ask: PendingAsk) => string;
  now: number;
}

export function NeedsYouSection({ asks, inbox, phone, agentLabel, now }: NeedsYouSectionProps) {
  const [all, setAll] = useState(false);
  if (asks.length === 0 && inbox === 0) return null;
  const folded = phone && !all && asks.length > PHONE_LIMIT;
  const shown = folded ? asks.slice(0, PHONE_LIMIT) : asks;

  return (
    <section className={cx(styles.section, !phone && styles.full)}>
      <SectionHeading title="Needs you" count={asks.length + inbox} dense={phone} />
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
          <ActionChip icon="chevron-down" onClick={() => setAll(true)}>
            Show {asks.length - PHONE_LIMIT} more
          </ActionChip>
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
