import type { TodoProgress } from "@waypoint/web-core/todos";

import { TodoItems } from "../cards/TodoItems";
import styles from "./TaskChecklist.module.css";

// The session's task list as the agent last wrote it.
export function TaskChecklist({ progress }: { progress: TodoProgress }) {
  return (
    <div className={styles.checklist}>
      <div
        className={styles.meter}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={progress.total}
        aria-valuenow={progress.completed}
        aria-label={`${progress.completed} of ${progress.total} tasks done`}
      >
        <span style={{ width: `${progress.total ? (progress.completed / progress.total) * 100 : 0}%` }} />
      </div>
      <TodoItems todos={progress.todos} className={styles.list} />
    </div>
  );
}
