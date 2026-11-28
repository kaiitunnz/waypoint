"use client";

import type { SessionStanding } from "@waypoint/web-core/sessionPresentation";

import { Sheet } from "@/components/ui/Dialog";

import { SessionStatusLine } from "../SessionStatusLine";
import { Inspector, type InspectorProps } from "./Inspector";
import styles from "./SessionSheet.module.css";

interface SessionSheetProps extends Omit<InspectorProps, "surface" | "identity"> {
  open: boolean;
  onClose: () => void;
  standing: SessionStanding | null;
  // "Claude Code, Opus 5.5, High".
  agent: string;
}

// The touch screen's session menu: who the session is, what it is doing and what can be done.
export function SessionSheet({ open, onClose, standing, agent, ...inspector }: SessionSheetProps) {
  const { session } = inspector;
  const identity = (
    <div className={styles.identity}>
      <h2 className={styles.title}>{session.title || "Session"}</h2>
      {standing ? <SessionStatusLine standing={standing} agent={agent} className={styles.status} /> : <span className={styles.status}>{agent}</span>}
    </div>
  );
  return (
    <Sheet open={open} onClose={onClose} title="Session details" header={identity} flush>
      <Inspector surface="sheet" {...inspector} />
    </Sheet>
  );
}
