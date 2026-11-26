import { describe, expect, it } from "vitest";

import { ev } from "./test-helpers";
import {
  currentTodoEvent,
  isTodoListEvent,
  isTodoToolEvent,
  readTodoEntries,
  summarizeTodos,
  type TodoEntry,
} from "./todos";

const todoWrite = (todos: unknown) =>
  ev(1, "tool_call", "", { tool_name: "TodoWrite", payload: { input: { todos } } });

describe("readTodoEntries", () => {
  it("is null for no event, no todos, or an empty list", () => {
    expect(readTodoEntries(null)).toBeNull();
    expect(readTodoEntries(undefined)).toBeNull();
    expect(readTodoEntries(ev(1, "tool_call", "", {}))).toBeNull();
    expect(readTodoEntries(todoWrite([]))).toBeNull();
    expect(readTodoEntries(todoWrite("nope"))).toBeNull();
  });

  it("reads Claude TodoWrite entries and maps in_progress", () => {
    const entries = readTodoEntries(
      todoWrite([
        { content: "Write parser", status: "completed" },
        { content: "Write tests", status: "in_progress", activeForm: "Writing tests" },
        { content: "Ship", status: "pending", description: "after review" },
      ]),
    );
    expect(entries).toEqual([
      { text: "Write parser", status: "completed", detail: undefined },
      { text: "Writing tests", status: "in-progress", detail: undefined },
      { text: "Ship", status: "pending", detail: "after review" },
    ]);
  });

  it("uses the imperative text unless the entry is in progress", () => {
    const [done, pending] = readTodoEntries(
      todoWrite([
        { content: "A", status: "completed", activeForm: "Doing A" },
        { content: "B", status: "pending", activeForm: "Doing B" },
      ]),
    )!;
    expect(done.text).toBe("A");
    expect(pending.text).toBe("B");
  });

  it("reads Codex todo_list items with text and completed", () => {
    const event = ev(1, "tool_result", "", {
      payload: { item: { items: [{ text: "one", completed: true }, { text: "two", completed: false }] } },
    });
    expect(readTodoEntries(event)).toEqual([
      { text: "one", status: "completed", detail: undefined },
      { text: "two", status: "pending", detail: undefined },
    ]);
  });

  it("falls back to metadata.tool_input and prefers text over content", () => {
    const event = ev(1, "tool_call", "", {
      tool_input: { todos: [{ text: "primary", content: "secondary" }] },
    });
    expect(readTodoEntries(event)).toEqual([{ text: "primary", status: "pending", detail: undefined }]);
  });

  it("drops entries that are not objects or have no text", () => {
    const entries = readTodoEntries(
      todoWrite([null, 3, "x", {}, { content: "" }, { content: "kept" }]),
    );
    expect(entries).toEqual([{ text: "kept", status: "pending", detail: undefined }]);
    expect(readTodoEntries(todoWrite([{ status: "pending" }]))).toBeNull();
  });

  it("prefers the item shape over the input shape", () => {
    const event = ev(1, "tool_result", "", {
      payload: { item: { items: [{ text: "from item" }] }, input: { todos: [{ content: "from input" }] } },
    });
    expect(readTodoEntries(event)?.[0].text).toBe("from item");
  });
});

describe("isTodoToolEvent", () => {
  it("is true when todo entries are readable or the tool is TodoWrite", () => {
    expect(isTodoToolEvent(todoWrite([{ content: "x" }]))).toBe(true);
    expect(isTodoToolEvent(ev(1, "tool_call", "", { tool_name: "TodoWrite" }))).toBe(true);
    expect(isTodoToolEvent(ev(1, "tool_call", "", { tool_name: "default_api:todowrite" }))).toBe(true);
  });

  it("is false for other events", () => {
    expect(isTodoToolEvent(null)).toBe(false);
    expect(isTodoToolEvent(undefined)).toBe(false);
    expect(isTodoToolEvent(ev(1, "tool_call", "", { tool_name: "Bash" }))).toBe(false);
  });
});

describe("isTodoListEvent", () => {
  it("matches todo_list items and TodoWrite calls", () => {
    expect(isTodoListEvent(ev(1, "tool_result", "", { item_type: "todo_list" }))).toBe(true);
    expect(isTodoListEvent(ev(1, "tool_call", "", { tool_name: "TodoWrite" }))).toBe(true);
    expect(isTodoListEvent(ev(1, "tool_call", "", { tool_name: "Bash" }))).toBe(false);
  });
});

describe("summarizeTodos", () => {
  const entry = (text: string, status: TodoEntry["status"]): TodoEntry => ({ text, status });

  it("is null for no todos", () => {
    expect(summarizeTodos(null)).toBeNull();
    expect(summarizeTodos(undefined)).toBeNull();
    expect(summarizeTodos([])).toBeNull();
  });

  it("surfaces the in-progress task, else the first pending, else nothing", () => {
    const working = summarizeTodos([entry("a", "completed"), entry("b", "pending"), entry("c", "in-progress")])!;
    expect(working.current?.text).toBe("c");
    expect(working.completed).toBe(1);
    expect(working.total).toBe(3);
    expect(working.allComplete).toBe(false);

    expect(summarizeTodos([entry("a", "completed"), entry("b", "pending"), entry("c", "pending")])!.current?.text).toBe("b");

    const done = summarizeTodos([entry("a", "completed")])!;
    expect(done.current).toBeNull();
    expect(done.allComplete).toBe(true);
  });
});

describe("currentTodoEvent", () => {
  const todo = (sequence: number) => ev(sequence, "tool_result", "todos", { item_type: "todo_list" });

  it("takes the latest in the window unless the loaded one is newer", () => {
    const events = [todo(2), ev(3, "agent_output", "x"), todo(4), ev(5, "agent_output", "y")];
    expect(currentTodoEvent(events, null)?.sequence).toBe(4);
    expect(currentTodoEvent(events, todo(3))?.sequence).toBe(4);
    expect(currentTodoEvent(events, todo(9))?.sequence).toBe(9);
  });

  it("falls back to the loaded one", () => {
    expect(currentTodoEvent([], todo(1))?.sequence).toBe(1);
    expect(currentTodoEvent([ev(1, "agent_output", "x")], null)).toBeNull();
  });
});
