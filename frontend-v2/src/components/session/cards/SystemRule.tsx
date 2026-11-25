import { EventTime } from "./EventTime";
import styles from "./SystemRule.module.css";

interface SystemRuleProps {
  text: string;
  ts: string;
  glyph?: string;
}

export function SystemRule({ text, ts, glyph }: SystemRuleProps) {
  return (
    <div className={styles.rule} role="note">
      <span className={styles.body}>
        {glyph ? <span aria-hidden="true">{glyph}</span> : null}
        <span className={styles.text}>{text}</span>
        <EventTime ts={ts} className={styles.time} />
      </span>
    </div>
  );
}
