import { TODO_MARKER, TODO_STATUS_LABEL, type TodoEntry } from "@waypoint/web-core/todos";

import { cx } from "@/lib/cx";

import styles from "./TodoItems.module.css";

export function TodoItems({ todos, className }: { todos: TodoEntry[]; className?: string }) {
  return (
    <ul className={cx(styles.list, className)}>
      {todos.map((todo, index) => (
        <li
          key={`${todo.text}-${index}`}
          className={cx(styles.item, todo.status === "completed" && styles.done, todo.status === "in-progress" && styles.now)}
        >
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
