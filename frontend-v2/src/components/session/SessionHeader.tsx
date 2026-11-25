"use client";

import type { ReactNode } from "react";

import type { SessionStanding } from "@waypoint/web-core/sessionPresentation";

import { GlassBar } from "@/components/ui/GlassBar";
import { Icon } from "@/components/ui/Icon";

import { PanelButton, type PanelControl } from "./PanelButton";
import { SessionStatusLine } from "./SessionStatusLine";
import styles from "./SessionHeader.module.css";

interface SessionHeaderProps {
  title: string;
  standing: SessionStanding | null;
  agent: string;
  onTitle: () => void;
  rail: PanelControl;
  pane: PanelControl;
  view: ReactNode;
  density: ReactNode;
  overflow: ReactNode;
}

export function SessionHeader({ title, standing, agent, onTitle, rail, pane, view, density, overflow }: SessionHeaderProps) {
  return (
    <GlassBar>
      <div className={styles.bar}>
        <PanelButton panel="rail" size="sm" control={rail} />
        <div className={styles.ttl}>
          <button
            type="button"
            className={styles.switch}
            onClick={onTitle}
            title="Switch session"
            aria-label={`${title}. Switch session`}
          >
            <span className={styles.name}>{title}</span>
            <Icon name="chevron-down" size={14} strokeWidth={2} />
          </button>
          {standing ? <SessionStatusLine standing={standing} agent={agent} className={styles.sub} /> : null}
        </div>
        {view}
        {density}
        <PanelButton panel="pane" size="sm" control={pane} />
        {overflow}
      </div>
    </GlassBar>
  );
}
