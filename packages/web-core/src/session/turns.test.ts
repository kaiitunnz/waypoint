import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import { buildTranscriptItems, mergeEvents } from "./transcript";
import { groupTurns, lastRunIndex, runCallsOf, type TranscriptTurn } from "./turns";

const call = (seq: number, id: string, tool = "Bash") => ev(seq, "tool_call", "", { item_id: id, tool_name: tool });

function turns(events: ReturnType<typeof ev>[]): TranscriptTurn[] {
  return groupTurns(buildTranscriptItems(events));
}
const shape = (list: TranscriptTurn[]) =>
  list.map((turn) => (turn.kind === "rule" ? "rule" : `${turn.kind}:${turn.items.length}`));

describe("groupTurns", () => {
  it("returns no turns for no events", () => {
    expect(groupTurns([])).toEqual([]);
  });

  it("groups consecutive messages of one author", () => {
    expect(
      shape(
        turns([
          ev(1, "user_input", "a"),
          ev(2, "user_input", "b"),
          ev(3, "agent_output", "c"),
          ev(4, "agent_output", "d"),
          ev(5, "user_input", "e"),
        ]),
      ),
    ).toEqual(["user:2", "agent:2", "user:1"]);
  });

  it("keeps tool runs and notes of an agent turn in the turn and routes it", () => {
    const list = turns([ev(1, "agent_output", "looking"), call(2, "a"), ev(3, "agent_output", "done")]);
    expect(shape(list)).toEqual(["agent:3"]);
    expect(list[0]).toMatchObject({ kind: "agent", routed: true });
  });

  it("does not route a turn of plain messages", () => {
    expect(turns([ev(1, "agent_output", "hi")])[0]).toMatchObject({ kind: "agent", routed: false });
  });

  it("stands a system note between turns as a rule", () => {
    expect(
      shape(turns([ev(1, "agent_output", "a"), ev(2, "system_note", "Session attached"), ev(3, "agent_output", "b")])),
    ).toEqual(["agent:1", "rule", "agent:1"]);
  });

  it("leaves out what renders nothing", () => {
    expect(
      shape(
        turns([
          ev(1, "user_input", "a"),
          ev(2, "approval_request", "ok?"),
          ev(3, "agent_output", "   "),
          ev(4, "system_note", "x", { method: "model.change", reason: "initial_mismatch" }),
          ev(5, "user_input", "b"),
        ]),
      ),
    ).toEqual(["user:2"]);
  });

  it("puts your answer to a question on your side", () => {
    expect(shape(turns([ev(1, "agent_output", "q"), ev(2, "user_input", "A", { kind: "ask_user_question_answer" })]))).toEqual([
      "agent:1",
      "user:1",
    ]);
  });

  it("gives each turn a stable key", () => {
    const first = turns([ev(1, "user_input", "a"), ev(2, "agent_output", "b")]);
    const again = turns([ev(1, "user_input", "a"), ev(2, "agent_output", "b"), ev(3, "agent_output", "c")]);
    expect(again.map((turn) => turn.key)).toEqual(first.map((turn) => turn.key));
  });

  it("keeps the key of a message while its deltas stream in", () => {
    const delta = (seq: number, text: string) => ev(seq, "agent_output", text, { item_id: "m" });
    const start = turns([delta(1, "Hi ")]);
    const streamed = turns(mergeEvents([delta(1, "Hi ")], delta(2, "there")));
    expect(streamed.map((turn) => turn.key)).toEqual(start.map((turn) => turn.key));
  });
});

describe("lastRunIndex", () => {
  const sent = (seq: number) => call(seq, `file-${seq}`, "SendUserFile");

  it("finds the turn's last run", () => {
    const list = turns([ev(1, "user_input", "go"), call(2, "a"), ev(3, "agent_output", "done")]);
    expect(lastRunIndex(list[0])).toBe(-1);
    expect(lastRunIndex(list[1])).toBe(0);
  });

  it("passes over a card that follows the run", () => {
    const [turn] = turns([call(1, "a"), sent(2)]);
    expect(turn.kind === "agent" && turn.items.map((item) => runCallsOf(item).length)).toEqual([1, 0]);
    expect(lastRunIndex(turn)).toBe(0);
  });

});

describe("runCallsOf", () => {
  it("counts a tool pair standing alone", () => {
    const [pair] = buildTranscriptItems([call(1, "a")]).flatMap((item) => (item.kind === "tool_run" ? item.items : []));
    expect(runCallsOf(pair)).toEqual([pair]);
  });
});
