import styles from "./SystemRule.module.css";

interface SystemRuleProps {
  text: string;
  time: string;
  glyph?: string;
}

// A system note or model switch: one centred line between the turns.
export function SystemRule({ text, time, glyph }: SystemRuleProps) {
  return (
    <div className={styles.rule} role="note">
      <span className={styles.body} title={text}>
        {glyph ? <span aria-hidden="true">{glyph}</span> : null}
        <span className={styles.text}>{text}</span>
        <time className={styles.time}>{time}</time>
      </span>
    </div>
  );
}
