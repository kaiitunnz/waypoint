"use client";

import { foldLines } from "@waypoint/web-core/session/foldText";
import { formatCallDuration, type CallView } from "@waypoint/web-core/session/toolRun";

import { cx } from "@/lib/cx";

import styles from "./CallRow.module.css";
import { Clamped } from "./Clamped";
import { DiffBlock } from "./DiffBlock";
import { DiffStat } from "./DiffStat";
import { CommandText, Lines } from "./CommandText";
import { FoldedText } from "./FoldedText";
import { Attachments } from "./Attachments";
import { Markdown } from "./Markdown";
import { PathLabel } from "./PathLabel";
import { TodoItems } from "./TodoItems";

const PROSE_LINES = 8;

// What a subagent was asked, or what it reported.
function Prose({ caption, text }: { caption: string; text: string }) {
  const { total } = foldLines(text, Infinity);
  return (
    <>
      <div className={styles.caption}>{caption}</div>
      <Clamped lines={PROSE_LINES} more={total > PROSE_LINES ? `Show all ${total} lines` : undefined} frame={styles.prose}>
        <Markdown text={text} />
      </Clamped>
    </>
  );
}

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

  const cut = row.cut ? <div className={styles.cut}>Preview of a longer report</div> : null;

  return (
    <div className={styles.call}>
      <div className={styles.head}>
        <span className={styles.glyph} role="img" aria-label={row.toolLabel}>
          {row.glyph}
        </span>
        <div className={styles.subject} title={command || row.path || row.name ? undefined : row.title}>
          {row.name ? (
            <div className={styles.text} title={row.title}>
              {row.name.server ? <span className={styles.aside}>{row.name.server} · </span> : null}
              {row.name.tool}
            </div>
          ) : null}
          {!row.label ? null : command ? (
            <CommandText text={row.label} />
          ) : (
            <div className={cx(styles.text, row.mono && styles.mono)}>
              {row.path ? (
                <PathLabel dir={row.path.dir} base={row.path.base} full={row.title} />
              ) : row.agentType ? (
                <>
                  {row.label} <span className={styles.aside}>{row.agentType}</span>
                </>
              ) : (
                <Lines text={row.label} />
              )}
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
      {row.prompt ? (
        <div className={styles.inset}>
          <Prose caption="Prompt" text={row.prompt} />
        </div>
      ) : null}
      {row.result ? (
        <div className={styles.inset}>
          <Prose caption="Result" text={row.result} />
          {cut}
        </div>
      ) : null}
      {row.output ? (
        <div className={styles.inset}>
          <FoldedText text={row.output} className={styles.out} />
          {cut}
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
