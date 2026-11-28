import { summaryParts, type ShownTitle } from "@waypoint/web-core/session/summary";

import { InlineCode } from "./InlineCode";
import styles from "./SummaryText.module.css";

// A call's summary, with its machine strings in mono.
export function SummaryText({ text }: { text: string }) {
  return summaryParts(text).map((part, index) => (part.code ? <InlineCode key={index}>{part.text}</InlineCode> : part.text));
}

// A summary set as the heading of the call it describes.
export function SummaryHeading({ text }: { text: string }) {
  return (
    <span className={styles.heading}>
      <SummaryText text={text} />
    </span>
  );
}

// A title that is the agent's summary reads its backticks; any other is as written.
// `aside` adds what such a title leaves out, such as a subagent's kind.
export function TitleText({ title, aside }: { title: ShownTitle; aside?: string | null }) {
  return (
    <>
      {title.marked ? <SummaryText text={title.text} /> : title.text}
      {aside ? <span className={styles.aside}> {aside}</span> : null}
    </>
  );
}
