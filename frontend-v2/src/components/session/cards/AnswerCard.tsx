import { askAnswerEntries } from "@waypoint/web-core/session/cardText";
import type { EventRecord } from "@waypoint/web-core/types";

import { Bubble } from "./Bubble";
import styles from "./AnswerCard.module.css";

// Your answers to the agent's questions, as a message from you.
export function AnswerCard({ event, time, first }: { event: EventRecord; time: string; first: boolean }) {
  const entries = askAnswerEntries(event);
  if (entries.length === 0) {
    // Older events pre-date the structured payload: the raw text Claude received.
    return <Bubble tone="user" first={first} text={event.text} time={time} />;
  }
  return (
    <Bubble tone="user" first={first} time={time} copy={event.text}>
      <ul className={styles.list}>
        {entries.map((entry, index) => (
          <li key={index}>
            <span className={styles.question}>{entry.question}</span>
            <span>{entry.answer ?? <span className={styles.none}>No option selected</span>}</span>
            {entry.notes ? <span className={styles.note}>{entry.notes}</span> : null}
          </li>
        ))}
      </ul>
    </Bubble>
  );
}
