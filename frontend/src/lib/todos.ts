import { EventRecord } from "@/lib/types";
import { normalizeToolName } from "@/lib/events";

export type TodoStatus = "completed" | "in-progress" | "pending";

export interface TodoEntry {
  text: string;
  status: TodoStatus;
  detail?: string;
}

export const TODO_MARKER: Record<TodoStatus, string> = {
  completed: "✓",
  "in-progress": "◐",
  pending: "○",
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function toolNameOf(event: EventRecord): string | null {
  const meta = asRecord(event.metadata);
  const name = meta?.tool_name;
  return typeof name === "string" && name ? normalizeToolName(name) : null;
}

export function isTodoToolEvent(event: EventRecord | null | undefined): boolean {
  if (!event) {
    return false;
  }
  return readTodoEntries(event)?.length ? true : toolNameOf(event) === "TodoWrite";
}

export function readTodoEntries(event: EventRecord | null | undefined): TodoEntry[] | null {
  if (!event) {
    return null;
  }
  const meta = asRecord(event.metadata);
  const payload = asRecord(meta?.payload);
  const input = asRecord(payload?.input) ?? asRecord(meta?.tool_input);
  const item = asRecord(payload?.item);
  const rawTodos =
    (Array.isArray(item?.items) ? item.items : null) ??
    (Array.isArray(input?.todos) ? input.todos : null);
  if (!rawTodos || rawTodos.length === 0) {
    return null;
  }
  const todos = rawTodos
    .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === "object")
    .map((entry) => {
      // Cross-backend shapes: Codex's native todo_list items use
      // `text`/`completed`, its update_plan todos and Claude's TodoWrite use
      // `text`/`content` with a `status` that includes `in_progress`. Read all
      // of them so one card renders every backend.
      const content =
        typeof entry.text === "string" && entry.text
          ? entry.text
          : typeof entry.content === "string"
            ? entry.content
            : "";
      let status: TodoStatus;
      if (entry.completed === true || entry.status === "completed") {
        status = "completed";
      } else if (entry.status === "in_progress") {
        status = "in-progress";
      } else {
        status = "pending";
      }
      // While in progress, prefer the present-tense `activeForm` ("Writing
      // the parser") over the imperative subject, matching how Claude renders
      // its own spinner. Carried by both TodoWrite and the Task tools.
      const activeForm = typeof entry.activeForm === "string" ? entry.activeForm : "";
      const text = status === "in-progress" && activeForm ? activeForm : content;
      const detail = typeof entry.description === "string" ? entry.description : undefined;
      return { text, status, detail };
    })
    .filter((entry) => entry.text);
  return todos.length > 0 ? todos : null;
}

export interface TodoProgress {
  todos: TodoEntry[];
  total: number;
  completed: number;
  // The task to surface as "current": the in-progress one, else the first
  // pending one, else null (everything done).
  current: TodoEntry | null;
  allComplete: boolean;
}

export function summarizeTodos(
  todos: TodoEntry[] | null | undefined,
): TodoProgress | null {
  if (!todos || todos.length === 0) {
    return null;
  }
  const completed = todos.filter((todo) => todo.status === "completed").length;
  const current =
    todos.find((todo) => todo.status === "in-progress") ??
    todos.find((todo) => todo.status === "pending") ??
    null;
  return {
    todos,
    total: todos.length,
    completed,
    current,
    allComplete: completed === todos.length,
  };
}
