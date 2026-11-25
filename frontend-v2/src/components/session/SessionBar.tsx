"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import type { SessionStanding } from "@waypoint/web-core/sessionPresentation";

import { Badge } from "@/components/ui/Badge";
import { GlassBar } from "@/components/ui/GlassBar";
import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import { PanelButton, type PanelControl } from "./PanelButton";
import { SessionStatusLine } from "./SessionStatusLine";
import styles from "./SessionBar.module.css";

interface SessionBarProps {
  title: string;
  standing: SessionStanding | null;
  agent: string;
  othersNeedYou: number;
  onTitle: () => void;
  // Where there is room for the side panels beside the conversation.
  rail?: PanelControl;
  pane?: PanelControl;
  view: ReactNode;
  overflow: ReactNode;
}

// Touch has no nav rail, so this header holds the way back.
export function SessionBar({ title, standing, agent, othersNeedYou, onTitle, rail, pane, view, overflow }: SessionBarProps) {
  return (
    <GlassBar>
      <Link
        href="/"
        className={cx(styles.back, rail && styles.beforeToggle)}
        aria-label={othersNeedYou > 0 ? `All sessions, ${othersNeedYou} other${othersNeedYou === 1 ? "" : "s"} need you` : "All sessions"}
      >
        <Icon name="chevron-left" size={22} />
        <Badge count={othersNeedYou} className={styles.badge} />
      </Link>
      {rail ? <PanelButton panel="rail" control={rail} /> : null}
      <button type="button" className={styles.title} onClick={onTitle} aria-label={standing ? `${title}. ${standing.word}. Switch session` : `${title}. Switch session`}>
        <span className={styles.nameRow}>
          <span className={styles.name}>{title}</span>
          <Icon name="chevron-down" size={14} strokeWidth={2} />
        </span>
        {standing ? <SessionStatusLine standing={standing} agent={agent} className={styles.sub} /> : null}
      </button>
      {view}
      {pane ? <PanelButton panel="pane" control={pane} /> : null}
      {overflow}
    </GlassBar>
  );
}
