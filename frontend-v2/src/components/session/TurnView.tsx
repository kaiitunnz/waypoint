"use client";

import { itemKey, type TranscriptTurn } from "@waypoint/web-core/session/turns";

import { cx } from "@/lib/cx";

import { ItemView } from "./ItemView";
import styles from "./TurnView.module.css";

interface TurnViewProps {
  turn: TranscriptTurn;
  full: boolean;
  // The session is working and this is the newest turn.
  working: boolean;
  structured: boolean;
}

// One turn: your messages on the right, the agent's work on the left with the
// route threading its tool runs, or a rule between them.
export function TurnView({ turn, full, working, structured }: TurnViewProps) {
  if (turn.kind === "rule") {
    return <ItemView item={turn.item} first={false} full={full} live={false} structured={structured} />;
  }
  const lastRun = working ? turn.items.findLastIndex((item) => item.kind === "tool_run" || item.kind === "pair") : -1;
  return (
    <div className={cx(styles.group, turn.kind === "user" ? styles.user : styles.agent, turn.kind === "agent" && turn.routed && styles.routed)}>
      {turn.items.map((item, index) => (
        <ItemView key={itemKey(item)} item={item} first={index === 0} full={full} live={index === lastRun} structured={structured} />
      ))}
    </div>
  );
}
