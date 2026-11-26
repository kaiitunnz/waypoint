"use client";

import { useRef, useState } from "react";

import type { TodoProgress } from "@waypoint/web-core/todos";

import { Panel } from "@/components/session/attention/Panel";
import { TaskChecklist } from "@/components/session/attention/TaskChecklist";
import { Button } from "@/components/ui/Button";
import { Flyout } from "@/components/ui/Flyout";

import styles from "./Composer.module.css";

interface TasksControlProps {
  tasks: TodoProgress;
  // Hides the tasks until the agent writes a newer list.
  onDismiss: () => void;
}

// ☑ done/total in the control row; it opens the whole checklist.
export function TasksControl({ tasks, onDismiss }: TasksControlProps) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={styles.tasks}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Tasks: ${tasks.completed} of ${tasks.total} done`}
        onClick={() => setOpen(!open)}
      >
        ☑ {tasks.completed}/{tasks.total}
      </button>
      <Flyout open={open} onClose={() => setOpen(false)} triggerRef={trigger} title="Tasks" subtitle={`${tasks.completed} of ${tasks.total} done`} width={360} side="top" align="right">
        <Panel
          title="Tasks"
          count={`${tasks.completed}/${tasks.total}`}
          actions={
            <Button
              variant="text"
              size="sm"
              onClick={() => {
                setOpen(false);
                onDismiss();
              }}
            >
              Dismiss
            </Button>
          }
        >
          <TaskChecklist progress={tasks} />
        </Panel>
      </Flyout>
    </>
  );
}
