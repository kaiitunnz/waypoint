"use client";

import { formatCallDuration, type CallView } from "@waypoint/web-core/session/toolRun";

import { cx } from "@/lib/cx";

import styles from "./CallRow.module.css";
import { DiffBlock } from "./DiffBlock";
import { DiffStat } from "./DiffStat";
import { CommandText, Lines } from "./CommandText";
import { FoldedText } from "./FoldedText";
import { Attachments } from "./Attachments";
import { PathLabel } from "./PathLabel";
import { TodoItems } from "./TodoItems";

export function CallRow({ row }: { row: CallView }) {
  const { todos } = row;
  const command = row.mono && !row.path;

  const status =
    row.status !== "failed" && row.additions !== null && row.deletions !== null ? (
      <DiffStat additions={row.additions} deletions={row.deletions} />
    ) : row.status === "pending" ? (
      <span className={styles.pending} role="img" aria-label="Running">
        …
      </span>
    ) : (
      <span className={row.status === "failed" ? styles.bad : styles.ok}>
        <span role="img" aria-label={row.status === "failed" ? "Failed" : "Done"}>
          {row.status === "failed" ? "✕" : "✓"}
        </span>
        {row.durationMs !== null ? ` ${formatCallDuration(row.durationMs)}` : ""}
      </span>
    );

  return (
    <div className={styles.call}>
      <div className={styles.head}>
        <span className={styles.glyph} role="img" aria-label={row.toolLabel}>
          {row.glyph}
        </span>
        <div className={styles.subject} title={command || row.path ? undefined : row.title}>
          {command ? (
            <CommandText text={row.label} />
          ) : (
            <div className={cx(styles.text, row.mono && styles.mono)}>
              {row.path ? <PathLabel dir={row.path.dir} base={row.path.base} full={row.title} /> : <Lines text={row.label} />}
            </div>
          )}
        </div>
        <span className={styles.status}>{status}</span>
      </div>
      {todos && todos.length > 0 ? <TodoItems todos={todos} className={styles.todos} /> : null}
      {row.diff ? (
        <div className={styles.inset}>
          <DiffBlock preview={row.diff} />
        </div>
      ) : null}
      {row.output ? (
        <div className={styles.inset}>
          <FoldedText text={row.output} keep="end" className={styles.out} />
        </div>
      ) : null}
      {row.attachments.length > 0 ? (
        <div className={styles.inset}>
          <Attachments specs={row.attachments} />
        </div>
      ) : null}
    </div>
  );
}
