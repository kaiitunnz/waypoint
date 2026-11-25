"use client";

import { useState } from "react";

import { foldLines } from "@waypoint/web-core/session/foldText";
import { formatCallDuration, type CallRow as CallRowModel } from "@waypoint/web-core/session/toolRun";
import { TODO_MARKER, type TodoEntry } from "@waypoint/web-core/todos";

import { cx } from "@/lib/cx";

import styles from "./CallRow.module.css";
import { Disclosure } from "./Disclosure";
import { DiffBlock } from "./DiffBlock";
import { DiffStat } from "./DiffStat";
import { FoldedText } from "./FoldedText";
import { Attachments } from "./Attachments";
import { PathLabel } from "./PathLabel";

const COMMAND_LINES = 6;
const OUTPUT_LINES = 4;

function TodoItems({ todos }: { todos: TodoEntry[] }) {
  return (
    <ul className={styles.todos}>
      {todos.map((todo, index) => (
        <li key={`${todo.text}-${index}`} className={cx(styles.todo, todo.status === "completed" && styles.todoDone, todo.status === "in-progress" && styles.todoNow)}>
          <span className={styles.mark} aria-hidden="true">
            {TODO_MARKER[todo.status]}
          </span>
          <span>{todo.text}</span>
        </li>
      ))}
    </ul>
  );
}

// Each line of a text is its own block, so a wrapped line hangs under its own start.
function Lines({ text }: { text: string }) {
  return text.split("\n").map((line, index) => (
    <span key={index} className={styles.ln}>
      {line || " "}
    </span>
  ));
}

function Command({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const folded = foldLines(text, COMMAND_LINES, "start");
  return (
    <>
      <div className={cx(styles.text, styles.mono)}>
        <Lines text={open ? text : folded.text} />
      </div>
      {folded.hidden > 0 ? <Disclosure open={open} total={folded.total} onToggle={() => setOpen(!open)} /> : null}
    </>
  );
}

export function CallRow({ row }: { row: CallRowModel }) {
  const { todos } = row;
  const command = row.mono && !row.path;

  const status =
    row.status !== "failed" && row.additions !== null && row.deletions !== null ? (
      <DiffStat additions={row.additions} deletions={row.deletions} />
    ) : row.status === "pending" ? (
      <span className={styles.pending}>…</span>
    ) : (
      <span className={row.status === "failed" ? styles.bad : styles.ok}>
        {row.status === "failed" ? "✕" : "✓"}
        {row.durationMs !== null ? ` ${formatCallDuration(row.durationMs)}` : ""}
      </span>
    );

  return (
    <div className={styles.call}>
      <div className={styles.head}>
        <span className={styles.glyph} aria-hidden="true">
          {row.glyph}
        </span>
        <div className={styles.subject} title={command ? undefined : row.title}>
          {command ? (
            <Command text={row.label} />
          ) : (
            <div className={cx(styles.text, row.mono && styles.mono)}>
              {row.path ? <PathLabel dir={row.path.dir} base={row.path.base} /> : <Lines text={row.label} />}
            </div>
          )}
        </div>
        <span className={styles.status}>{status}</span>
      </div>
      {todos && todos.length > 0 ? <TodoItems todos={todos} /> : null}
      {row.diff ? (
        <div className={styles.inset}>
          <DiffBlock preview={row.diff} />
        </div>
      ) : null}
      {row.output ? (
        <div className={styles.inset}>
          <FoldedText text={row.output} lines={OUTPUT_LINES} keep="end" className={styles.out} />
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
