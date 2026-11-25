"use client";

import type { ReactNode } from "react";

import type { SessionStanding } from "@waypoint/web-core/sessionPresentation";

import { GlassBar } from "@/components/ui/GlassBar";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";

import { SessionStatusLine } from "./SessionStatusLine";
import styles from "./SessionHeader.module.css";

interface SessionHeaderProps {
  title: string;
  standing: SessionStanding | null;
  agent: string;
  onTitle: () => void;
  railOpen: boolean;
  onToggleRail: () => void;
  paneOpen: boolean;
  onTogglePane: () => void;
  view: ReactNode;
  density: ReactNode;
  overflow: ReactNode;
}

export function SessionHeader({
  title,
  standing,
  agent,
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
          <button
            type="button"
            className={styles.switch}
            onClick={onTitle}
            title="Switch session"
            aria-label={`${title || "Session"}. Switch session`}
          >
            <span className={styles.name}>{title || "Session"}</span>
            <Icon name="chevron-down" size={14} strokeWidth={2} />
          </button>
          {standing ? <SessionStatusLine standing={standing} agent={agent} className={styles.sub} /> : null}
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
