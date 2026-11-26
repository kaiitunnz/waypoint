"use client";

import { formatCallDuration, type CallView } from "@waypoint/web-core/session/toolRun";
import { TODO_MARKER, TODO_STATUS_LABEL, type TodoEntry } from "@waypoint/web-core/todos";

import { cx } from "@/lib/cx";

import styles from "./CallRow.module.css";
import clamp from "./Clamp.module.css";
import { Disclosure } from "./Disclosure";
import { DiffBlock } from "./DiffBlock";
import { DiffStat } from "./DiffStat";
import { FoldedText } from "./FoldedText";
import { Attachments } from "./Attachments";
import { PathLabel } from "./PathLabel";
import { useClamp } from "./useClamp";
import { useFold } from "./useFold";

function TodoItems({ todos }: { todos: TodoEntry[] }) {
  return (
    <ul className={styles.todos}>
      {todos.map((todo, index) => (
        <li key={`${todo.text}-${index}`} className={cx(styles.todo, todo.status === "completed" && styles.todoDone, todo.status === "in-progress" && styles.todoNow)}>
          <span className={styles.mark} role="img" aria-label={TODO_STATUS_LABEL[todo.status]}>
            {TODO_MARKER[todo.status]}
          </span>
          <span>
            {todo.text}
            {todo.detail ? <span className={styles.detail}>{todo.detail}</span> : null}
          </span>
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
  const fold = useFold(text, "start");
  const { content, chip } = useClamp<HTMLDivElement>(fold.open || fold.hidden > 0);
  return (
    <>
      <div className={cx(styles.text, styles.mono, !fold.open && clamp.clamp)}>
        <div ref={content}>
          <Lines text={fold.shown} />
        </div>
      </div>
      <div ref={chip}>
        <Disclosure open={fold.open} total={fold.hidden > 0 ? fold.total : undefined} onToggle={fold.toggle} />
      </div>
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

  return (
    <div className={styles.call}>
      <div className={styles.head}>
        <span className={styles.glyph} role="img" aria-label={row.toolLabel}>
          {row.glyph}
        </span>
        <div className={styles.subject} title={command || row.path ? undefined : row.title}>
          {command ? (
            <Command text={row.label} />
          ) : (
            <div className={cx(styles.text, row.mono && styles.mono)}>
              {row.path ? <PathLabel dir={row.path.dir} base={row.path.base} full={row.title} /> : <Lines text={row.label} />}
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
