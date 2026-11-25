"use client";

import { useState } from "react";

import { TODO_MARKER } from "@waypoint/web-core/todos";
import { formatCallDuration, type CallRow as CallRowModel } from "@waypoint/web-core/session/toolRun";
import type { TodoEntry } from "@waypoint/web-core/todos";

import { cx } from "@/lib/cx";

import styles from "./CallRow.module.css";
import { DiffBlock } from "./DiffBlock";
import { PathLabel } from "./PathLabel";

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

// One call of an expanded run: what it touched, what it did, and how it went.
export function CallRow({ row }: { row: CallRowModel }) {
  const [wrapped, setWrapped] = useState(false);
  const { todos } = row;

  const status =
    row.additions !== null && row.deletions !== null ? (
      <>
        <span className={styles.add}>+{row.additions}</span> <span className={styles.del}>−{row.deletions}</span>
      </>
    ) : row.status === "pending" ? (
      <span className={styles.pending}>…</span>
    ) : (
      <span className={row.status === "failed" ? styles.del : styles.add}>
        {row.status === "failed" ? "✕" : "✓"}
        {row.durationMs !== null ? ` ${formatCallDuration(row.durationMs)}` : ""}
      </span>
    );

  const line = (
    <>
      <span className={styles.glyph} aria-hidden="true">
        {row.glyph}
      </span>
      {row.path ? (
        <PathLabel dir={row.path.dir} base={row.path.base} className={styles.label} />
      ) : (
        <span className={cx(styles.label, row.mono && styles.mono, wrapped && styles.wrapped)}>{row.label}</span>
      )}
      <span className={styles.status}>{status}</span>
    </>
  );

  return (
    <>
      {/* A command or pattern can be long: its row opens to show it whole. */}
      {row.path || todos ? (
        <div className={styles.call} title={row.title}>
          {line}
        </div>
      ) : (
        <button type="button" className={cx(styles.call, styles.tappable)} onClick={() => setWrapped((value) => !value)} aria-expanded={wrapped} title={row.title}>
          {line}
        </button>
      )}
      {todos && todos.length > 0 ? <TodoItems todos={todos} /> : null}
      {row.diff ? <DiffBlock preview={row.diff} /> : null}
      {row.output ? <div className={cx(styles.out, row.status === "failed" && styles.outFailed)}>{row.output}</div> : null}
    </>
  );
}
