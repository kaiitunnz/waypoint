import type { ReactNode } from "react";

import type { AttachmentSpec } from "@waypoint/web-core/types";

import { cx } from "@/lib/cx";

import { Attachments } from "./Attachments";
import styles from "./Bubble.module.css";
import { CopyButton } from "./CopyButton";
import { EventTime } from "./EventTime";
import { Markdown } from "./Markdown";

interface BubbleProps {
  tone: "agent" | "user";
  // The first agent bubble of a turn has the sharp origin corner; a user bubble always does.
  first?: boolean;
  text?: string;
  ts: string;
  // What the copy button copies, when it differs from `text`.
  copy?: string;
  attachments?: AttachmentSpec[];
  // Still on its way: drawn faded, with nothing to copy yet.
  pending?: boolean;
  children?: ReactNode;
}

// A message. The time and copy button sit inside it, on the last line when it
// fits; otherwise on a row of their own.
export function Bubble({ tone, first, text, ts, copy, attachments = [], pending, children }: BubbleProps) {
  const spare = attachments.length > 0 || children !== undefined;
  return (
    <div className={cx(styles.bub, tone === "user" && styles.user, first && styles.first, pending && styles.pending)}>
      {text !== undefined ? <Markdown text={text} reserve={!spare} /> : null}
      {children}
      <Attachments specs={attachments} />
      {spare ? <div className={styles.row} /> : null}
      <span className={styles.meta}>
        <EventTime ts={ts} />
        {!pending && (copy !== undefined || text !== undefined) ? <CopyButton text={copy ?? text ?? ""} /> : null}
      </span>
    </div>
  );
}
