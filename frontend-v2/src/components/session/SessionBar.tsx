"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import type { SessionStanding } from "@waypoint/web-core/sessionPresentation";

import { Badge } from "@/components/ui/Badge";
import { GlassBar } from "@/components/ui/GlassBar";
import { Icon } from "@/components/ui/Icon";

import { SessionStatusLine } from "./SessionStatusLine";
import styles from "./SessionBar.module.css";

interface SessionBarProps {
  title: string;
  standing: SessionStanding | null;
  agent: string;
  othersNeedYou: number;
  onTitle: () => void;
  switcher: ReactNode;
  overflow: ReactNode;
}

export function SessionBar({ title, standing, agent, othersNeedYou, onTitle, switcher, overflow }: SessionBarProps) {
  return (
    <GlassBar>
      <Link
        href="/"
        className={styles.back}
        aria-label={othersNeedYou > 0 ? `All sessions, ${othersNeedYou} other${othersNeedYou === 1 ? "" : "s"} need you` : "All sessions"}
      >
        <Icon name="chevron-left" size={22} />
        <Badge count={othersNeedYou} className={styles.badge} />
      </Link>
      <button type="button" className={styles.title} onClick={onTitle} aria-label={`${title || "Session"}. Switch session`}>
        <span className={styles.nameRow}>
          <span className={styles.name}>{title || "Session"}</span>
          <Icon name="chevron-down" size={14} strokeWidth={2} />
        </span>
        {standing ? <SessionStatusLine standing={standing} agent={agent} className={styles.sub} /> : null}
      </button>
      {switcher}
      {overflow}
    </GlassBar>
  );
}
