import type { SessionStanding } from "@waypoint/web-core/sessionPresentation";

import { StatusLamp } from "@/components/ui/StatusLamp";
import { cx } from "@/lib/cx";

import styles from "./SessionStatusLine.module.css";

interface SessionStatusLineProps {
  standing: SessionStanding;
  // "Claude Code, Opus 5.5".
  agent: string;
  className?: string;
}

export function SessionStatusLine({ standing, agent, className }: SessionStatusLineProps) {
  return (
    <span className={className}>
      <span className={cx(styles.state, standing.lamp === "needs" && styles.needs)}>
        {standing.lamp === "exited" ? null : <StatusLamp state={standing.lamp} size="sm" flat />}
        {standing.word}
      </span>
      <span className={styles.agent}>{agent}</span>
    </span>
  );
}
