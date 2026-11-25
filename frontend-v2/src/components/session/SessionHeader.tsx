"use client";

import type { ReactNode } from "react";

import { GlassBar } from "@/components/ui/GlassBar";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { StatusLamp } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";

import type { SessionStatus } from "./SessionBar";
import styles from "./SessionHeader.module.css";

interface SessionHeaderProps {
  title: string;
  status: SessionStatus | null;
  onTitle: () => void;
  railOpen: boolean;
  onToggleRail: () => void;
  paneOpen: boolean;
  onTogglePane: () => void;
  view: ReactNode;
  density: ReactNode;
  overflow: ReactNode;
}

// The desktop header: it floats over the top of the conversation column.
export function SessionHeader({
  title,
  status,
  onTitle,
  railOpen,
  onToggleRail,
  paneOpen,
  onTogglePane,
  view,
  density,
  overflow,
}: SessionHeaderProps) {
  return (
    <GlassBar>
      <div className={styles.bar}>
        <IconButton label="Sessions" size="sm" onClick={onToggleRail} aria-pressed={railOpen}>
          <Icon name="panel-left" size={18} />
        </IconButton>
        <div className={styles.ttl}>
          <button type="button" className={styles.name} onClick={onTitle} title="Switch session">
            {title || "Session"}
          </button>
          {status ? (
            <span className={styles.sub}>
              <span className={cx(styles.state, status.lamp === "needs" && styles.needs)}>
                <StatusLamp state={status.lamp} size="sm" flat />
                {status.word}
              </span>
              <span className={styles.agent}>{status.agent}</span>
            </span>
          ) : null}
        </div>
        {view}
        {density}
        <IconButton label="Side pane" size="sm" onClick={onTogglePane} aria-pressed={paneOpen}>
          <Icon name="panel-right" size={18} />
        </IconButton>
        {overflow}
      </div>
    </GlassBar>
  );
}
