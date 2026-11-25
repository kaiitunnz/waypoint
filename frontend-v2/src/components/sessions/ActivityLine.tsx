import type { SessionActivity } from "@waypoint/web-core/activity";

import { Meter } from "@/components/ui/Meter";

import styles from "./ActivityLine.module.css";

interface ActivityLineProps {
  fallback: string;
  activity?: SessionActivity;
  // A finished turn reads brighter than a running step.
  bright?: boolean;
  // "4/7" on a row, "4 of 7" on a tile.
  progressStyle?: "short" | "long";
}

export function ActivityLine({ fallback, activity, bright, progressStyle = "short" }: ActivityLineProps) {
  const progress = activity?.progress ?? null;
  return (
    <span className={bright ? styles.bright : styles.line}>
      <span className={styles.text}>{activity?.line ?? fallback}</span>
      {progress ? (
        <>
          <Meter value={(progress.done / progress.total) * 100} label="Tasks done" />
          <span className={styles.count}>
            {progressStyle === "long" ? `${progress.done} of ${progress.total}` : `${progress.done}/${progress.total}`}
          </span>
        </>
      ) : null}
    </span>
  );
}
