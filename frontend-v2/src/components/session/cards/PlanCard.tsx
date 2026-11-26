import { Clamped } from "./Clamped";
import { CopyButton } from "./CopyButton";
import { EventTime } from "./EventTime";
import { Markdown } from "./Markdown";
import styles from "./PlanCard.module.css";

export function PlanCard({ plan, ts }: { plan: string; ts: string }) {
  return (
    <div className={styles.card}>
      <div className={styles.head}>
        <span className={styles.title}>Plan</span>
        <EventTime ts={ts} className={styles.time} />
        <CopyButton text={plan} label="Copy plan" />
      </div>
      <Clamped lines={8} className={styles.body}>
        <Markdown text={plan} />
      </Clamped>
    </div>
  );
}
