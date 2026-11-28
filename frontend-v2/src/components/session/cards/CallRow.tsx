"use client";

import { formatCallDuration, type CallView } from "@waypoint/web-core/session/toolRun";

import { SummaryText } from "@/components/ui/SummaryText";
import { cx } from "@/lib/cx";

import styles from "./CallRow.module.css";
import { Clamped } from "./Clamped";
import { DiffBlock } from "./DiffBlock";
import { DiffStat } from "./DiffStat";
import { CommandText, Lines, Prompt } from "./CommandText";
import { FoldedText } from "./FoldedText";
import { Attachments } from "./Attachments";
import { Markdown } from "./Markdown";
import { PathLabel } from "./PathLabel";
import { TodoItems } from "./TodoItems";
import { ToolGlyph } from "./ToolGlyph";
import { FOLD_LINES } from "./useFold";

function Prose({ caption, text }: { caption: string; text: string }) {
  return (
    <>
      <div className={styles.caption}>{caption}</div>
      <Clamped lines={FOLD_LINES} frame={styles.prose}>
        <Markdown text={text} />
      </Clamped>
    </>
  );
}

export function CallRow({ row }: { row: CallView }) {
  const { todos, reply } = row;
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
        <ToolGlyph className={styles.glyph} glyph={row.glyph} family={row.family} role="img" aria-label={row.toolLabel} />
        <div className={styles.subject} title={command || row.path || row.name ? undefined : row.title}>
          {row.summary ? (
            <div className={styles.heading}>
              <SummaryText text={row.summary} />
              {row.agentType && !row.label ? <span className={styles.aside}> {row.agentType}</span> : null}
            </div>
          ) : null}
          {row.name ? (
            <div className={styles.text} title={row.title}>
              {row.name.server ? <span className={styles.aside}>{row.name.server} · </span> : null}
              {row.name.tool}
            </div>
          ) : null}
          {!row.label ? null : row.shell && row.summary !== null ? (
            // Under a heading the tool's glyph is a line up, so the command takes a prompt there.
            <div className={styles.shell}>
              <Prompt className={styles.prompt} />
              <CommandText text={row.label} />
            </div>
          ) : command ? (
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
      {reply ? (
        <div className={styles.inset}>
          {reply.kind === "report" ? (
            <Prose caption="Result" text={reply.text} />
          ) : (
            <FoldedText text={reply.text} className={styles.out} />
          )}
          {reply.cut ? (
            <div className={styles.cut}>{reply.kind === "report" ? "Preview of a longer report" : "Preview of longer output"}</div>
          ) : null}
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
