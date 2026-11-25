"use client";

import { useState } from "react";

import { readTodoEntries, summarizeTodos, TODO_MARKER } from "@waypoint/web-core/todos";
import { formatCallDuration, type CallRow as CallRowModel } from "@waypoint/web-core/session/toolRun";
import type { TodoEntry } from "@waypoint/web-core/todos";
import type { EventRecord } from "@waypoint/web-core/types";

import { cx } from "@/lib/cx";

import styles from "./CallRow.module.css";
import { DiffBlock } from "./DiffBlock";

interface CallRowProps {
  row: CallRowModel;
  // The call's result, else its call, for a todo list's entries.
  source: EventRecord | null;
}

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
export function CallRow({ row, source }: CallRowProps) {
  const [wrapped, setWrapped] = useState(false);
  const todos = row.tool === "TodoWrite" ? readTodoEntries(source) : null;
  const progress = todos ? summarizeTodos(todos) : null;
  const label = todos ? (progress ? `Tasks ${progress.completed} of ${progress.total} done` : "Tasks") : row.label;

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

  return (
    <>
      <button type="button" className={styles.call} onClick={() => setWrapped((value) => !value)} aria-expanded={wrapped} title={row.label}>
        <span className={styles.glyph} aria-hidden="true">
          {row.glyph}
        </span>
        {row.path ? (
          <span className={cx(styles.label, styles.mono)}>
            <span className={styles.dir}>{row.path.dir}</span>
            <span className={styles.base}>{row.path.base}</span>
          </span>
        ) : (
          <span className={cx(styles.label, row.mono && styles.mono, wrapped && styles.wrapped)}>{label}</span>
        )}
        <span className={styles.status}>{status}</span>
      </button>
      {todos && todos.length > 0 ? <TodoItems todos={todos} /> : null}
      {row.diff ? <DiffBlock preview={row.diff} /> : null}
      {row.output ? <div className={cx(styles.out, row.status === "failed" && styles.outFailed)}>{row.output}</div> : null}
    </>
  );
}
