import type { TodoProgress } from "@waypoint/web-core/todos";

import { TodoItems } from "@/components/session/cards/TodoItems";
import { Meter } from "@/components/ui/Meter";

import styles from "./TaskChecklist.module.css";

export function TaskChecklist({ progress }: { progress: TodoProgress }) {
  return (
    <div className={styles.checklist}>
      <Meter
        value={progress.total ? (progress.completed / progress.total) * 100 : 0}
        label={`${progress.completed} of ${progress.total} tasks done`}
        className={styles.meter}
      />
      <TodoItems todos={progress.todos} className={styles.list} />
    </div>
  );
}
