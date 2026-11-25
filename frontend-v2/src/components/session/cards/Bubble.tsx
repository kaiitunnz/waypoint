import type { ReactNode } from "react";

import type { AttachmentSpec } from "@waypoint/web-core/types";

import { cx } from "@/lib/cx";

import { Attachments } from "./Attachments";
import styles from "./Bubble.module.css";
import { CopyButton } from "./CopyButton";
import { Markdown } from "./Markdown";

interface BubbleProps {
  tone: "agent" | "user";
  // The first bubble of a turn has the sharp origin corner.
  first?: boolean;
  text?: string;
  time: string;
  // Sent and copied text; defaults to `text`.
  copy?: string;
  attachments?: AttachmentSpec[];
  // A body other than markdown.
  children?: ReactNode;
}

// A message. The time and copy button sit inside it, on the last line when it
// fits; otherwise on a short line of their own, so they cost no extra row.
export function Bubble({ tone, first, text, time, copy, attachments = [], children }: BubbleProps) {
  const spare = attachments.length > 0 || children !== undefined;
  return (
    <div className={cx(styles.bub, tone === "user" && styles.user, first && styles.first)}>
      {text !== undefined ? <Markdown text={text} reserve={!spare} /> : null}
      {children}
      <Attachments specs={attachments} />
      {spare ? <div className={styles.row} /> : null}
      <span className={styles.meta}>
        <time>{time}</time>
        {copy !== undefined || text !== undefined ? <CopyButton text={copy ?? text ?? ""} /> : null}
      </span>
    </div>
  );
}
