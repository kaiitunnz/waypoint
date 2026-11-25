import { describe, expect, it } from "vitest";

import { ev, planMetadata } from "../test-helpers";
import { cardKindOf, pairCardKindOf } from "./cardKind";

const pairOf = (call: ReturnType<typeof ev> | null, result: ReturnType<typeof ev> | null) => ({
  call,
  result,
  itemId: "i",
  ts: "t",
  sequence: 1,
});

const askInput = { questions: [{ question: "Which?", options: [{ label: "A" }, { label: "B" }] }] };

describe("cardKindOf", () => {
  it("separates your messages from your answers", () => {
    expect(cardKindOf(ev(1, "user_input", "hi"))).toBe("user_message");
    expect(cardKindOf(ev(2, "user_input", "A", { kind: "ask_user_question_answer" }))).toBe("ask_answer");
  });

  it("drops blank agent messages and tells reasoning apart", () => {
    expect(cardKindOf(ev(1, "agent_output", "  \n"))).toBe("none");
    expect(cardKindOf(ev(2, "agent_output", "hmm", { item_kind: "reasoning" }))).toBe("reasoning");
    expect(cardKindOf(ev(3, "agent_output", "hello"))).toBe("agent_message");
  });

  it("renders no card for an approval request", () => {
    expect(cardKindOf(ev(1, "approval_request", "run it?"))).toBe("none");
  });

  it("finds ask-question and send-file tool calls, paired or not", () => {
    const ask = ev(1, "tool_call", "", { tool_name: "AskUserQuestion", payload: { input: askInput } });
    expect(cardKindOf(ask)).toBe("ask_question");
    expect(pairCardKindOf(pairOf(ask, null))).toBe("ask_question");
    const send = ev(2, "tool_call", "", { tool_name: "SendUserFile", payload: { input: { files: ["/a"] } } });
    expect(cardKindOf(send)).toBe("send_file");
    expect(pairCardKindOf(pairOf(send, null))).toBe("send_file");
  });

  it("finds todo and ordinary tools", () => {
    const todo = ev(1, "tool_call", "", { tool_name: "TodoWrite", payload: { input: { todos: [] } } });
    expect(cardKindOf(todo)).toBe("todo");
    expect(pairCardKindOf(pairOf(todo, null))).toBe("todo");
    const bash = ev(2, "tool_call", "", { tool_name: "Bash" });
    expect(cardKindOf(bash)).toBe("tool");
    expect(cardKindOf(ev(3, "tool_result", "out"))).toBe("tool");
    expect(pairCardKindOf(pairOf(bash, ev(4, "tool_result", "out")))).toBe("tool");
  });

  it("classifies system notes", () => {
    expect(cardKindOf(ev(1, "system_note", "", planMetadata("p1")))).toBe("plan");
    expect(cardKindOf(ev(2, "system_note", "ok", { builtin_command: "/status" }))).toBe("status");
    expect(cardKindOf(ev(3, "system_note", "switched", { method: "model.change", reason: "switch", current_model: "x" }))).toBe(
      "model_switch",
    );
    expect(
      cardKindOf(ev(4, "system_note", "mismatch", { method: "model.change", reason: "initial_mismatch" })),
    ).toBe("none");
    expect(cardKindOf(ev(5, "system_note", "done", { method: "task_notification", task_notification: { version: 1, id: "n1", kind: "agent" } }))).toBe(
      "task_notification",
    );
    expect(
      cardKindOf(
        ev(6, "system_note", "changed", {
          diff_preview: { files: [{ path: "a", diff: "+x" }], total_additions: 1, total_deletions: 0 },
        }),
      ),
    ).toBe("diff_note");
    expect(cardKindOf(ev(7, "system_note", "Session attached"))).toBe("rule");
  });

  it("falls back for kinds with no card", () => {
    expect(cardKindOf(ev(1, "raw_terminal_chunk", "x"))).toBe("heuristic");
  });
});
