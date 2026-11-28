import type { TodoProgress } from "@waypoint/web-core/todos";

import { TodoItems } from "@/components/session/cards/TodoItems";

import { Section } from "./Section";
import styles from "./TasksSection.module.css";

// The agent's task list, as the composer's checklist shows it.
export function TasksSection({ progress }: { progress: TodoProgress }) {
  return (
    <Section title="Tasks" count={`${progress.completed} of ${progress.total}`}>
      <TodoItems todos={progress.todos} className={styles.list} />
    </Section>
  );
}
