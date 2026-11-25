"use client";

import { itemKey } from "@waypoint/web-core/session/transcript";
import type { TranscriptTurn } from "@waypoint/web-core/session/turns";

import { cx } from "@/lib/cx";

import { useSessionContext } from "./SessionContext";
import { ItemView } from "./ItemView";
import styles from "./TurnView.module.css";

interface TurnViewProps {
  turn: TranscriptTurn;
  // The session is working and this is the newest turn.
  working: boolean;
}

export function TurnView({ turn, working }: TurnViewProps) {
  const { agentName } = useSessionContext();
  if (turn.kind === "rule") return <ItemView item={turn.item} first={false} live={false} />;
  const lastRun = working ? turn.items.findLastIndex((item) => item.kind === "tool_run" || item.kind === "pair") : -1;
  return (
    <div
      role="group"
      aria-label={turn.kind === "user" ? "You" : agentName}
      className={cx(styles.group, turn.kind === "user" && styles.user, turn.kind === "agent" && turn.routed && styles.routed)}
    >
      {turn.items.map((item, index) => (
        <ItemView key={itemKey(item)} item={item} first={index === 0} live={index === lastRun} />
      ))}
    </div>
  );
}
