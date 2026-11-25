"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import type { SessionLamp } from "@waypoint/web-core/sessionPresentation";

import { Badge } from "@/components/ui/Badge";
import { GlassBar } from "@/components/ui/GlassBar";
import { Icon } from "@/components/ui/Icon";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";

import styles from "./SessionBar.module.css";

export interface SessionStatus {
  lamp: SessionLamp;
  // The state in words: "Needs you", "Working", "Reconnecting".
  word: string;
  // "Claude Code, Opus 5.5".
  agent: string;
  // "Opus 5.5": the desktop header keeps it when the full label has no room.
  model: string | null;
}

interface SessionBarProps {
  title: string;
  status: SessionStatus | null;
  // Other sessions waiting on the human.
  othersNeedYou: number;
  onTitle: () => void;
  switcher: ReactNode;
  overflow: ReactNode;
}

// The mobile top bar: it floats over the transcript, which scrolls under it.
export function SessionBar({ title, status, othersNeedYou, onTitle, switcher, overflow }: SessionBarProps) {
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
      <button type="button" className={styles.title} onClick={onTitle} aria-label={`${title}. Switch session`}>
        <span className={styles.name}>{title || "Session"}</span>
        {status ? (
          <span className={styles.sub}>
            <span className={cx(styles.state, status.lamp === "needs" && styles.needs)}>
              <StatusLamp state={status.lamp} size="sm" flat />
              {status.word}
            </span>
            <span className={styles.agent}>{status.agent}</span>
          </span>
        ) : null}
      </button>
      {switcher}
      {overflow}
    </GlassBar>
  );
}
