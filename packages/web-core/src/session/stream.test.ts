import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import type { EventsPage, PendingQuestionsSnapshot, SessionRecord } from "../types";
import {
  INITIAL_STREAM_STATE,
  streamReducer,
  maxEventId,
  takeNewer,
  type StreamAction,
  type StreamState,
} from "./stream";

const session = (title: string) => ({ id: "s1", title }) as SessionRecord;
const page = (events: ReturnType<typeof ev>[], extra: Partial<EventsPage> = {}): EventsPage => ({
  events,
  has_more: false,
  latest_todo: null,
  pending_questions: null,
  ...extra,
});
const snapshot = (asOf: number, revision: number) =>
  ({ as_of_sequence: asOf, revision, questions: [] }) as unknown as PendingQuestionsSnapshot;

const run = (...actions: StreamAction[]): StreamState => actions.reduce(streamReducer, INITIAL_STREAM_STATE);

describe("maxEventId", () => {
  it("takes the highest id, ignoring events without one", () => {
    expect(maxEventId([ev(3, "agent_output"), ev(9, "agent_output"), ev(5, "agent_output")])).toBe(9);
    expect(maxEventId([{ ...ev(4, "agent_output"), id: undefined }])).toBeNull();
  });

  it("keeps a higher starting point", () => {
    expect(maxEventId([ev(3, "agent_output")], 7)).toBe(7);
    expect(maxEventId([ev(8, "agent_output")], 7)).toBe(8);
  });
});

describe("takeNewer", () => {
  const events = [ev(5, "agent_output", "a"), ev(6, "agent_output", "b"), { ...ev(7, "agent_output", "c"), id: undefined }];

  it("keeps what is past the highest id, and what has no id", () => {
    expect(takeNewer(events, 5).map((event) => event.sequence)).toEqual([6, 7]);
    expect(takeNewer(events, 6).map((event) => event.sequence)).toEqual([7]);
  });

  it("keeps everything while nothing is known", () => {
    expect(takeNewer(events, null)).toHaveLength(3);
  });

  it("keeps a repeated delta out of a coalesced item", () => {
    const page = [ev(5, "agent_output", "A", { item_id: "m" }), ev(6, "agent_output", "B", { item_id: "m" }), ev(7, "agent_output", "C", { item_id: "m" })];
    const live = takeNewer([ev(6, "agent_output", "B", { item_id: "m" })], maxEventId(page));
    expect(live).toEqual([]);
  });
});

describe("streamReducer", () => {
  it("keeps a session frame that arrived before the page", () => {
    const state = run(
      { type: "session", session: session("socket") },
      { type: "loaded", session: session("http"), page: page([]) },
    );
    expect(state.session?.title).toBe("socket");
  });

  it("counts each error, even a repeated message", () => {
    const twice = run({ type: "error", message: "boom" }, { type: "error", message: "boom" });
    expect(twice.errorCount).toBe(2);
    expect(streamReducer(twice, { type: "error", message: null }).errorCount).toBe(2);
  });

  it("is not loaded until the tail page arrives, even with the session record", () => {
    const early = run({ type: "session", session: session("a") });
    expect(early.session?.title).toBe("a");
    expect(early.loaded).toBe(false);
    expect(streamReducer(early, { type: "loaded", session: session("a"), page: page([]) }).loaded).toBe(true);
  });

  it("keeps the status of a failed load until a load succeeds", () => {
    const failed = run({ type: "error", message: "not found", status: 404 });
    expect(failed.errorStatus).toBe(404);
    expect(streamReducer(failed, { type: "error", message: null }).errorStatus).toBeNull();
    expect(streamReducer(failed, { type: "loaded", session: session("a"), page: page([]) }).errorStatus).toBeNull();
  });

  it("loads the tail page, coalescing streamed deltas and stripping ANSI", () => {
    const state = run({
      type: "loaded",
      session: session("a"),
      page: page(
        [
          ev(5, "agent_output", "Hi ", { item_id: "m" }),
          ev(6, "agent_output", "there", { item_id: "m" }),
          ev(7, "tool_result", "\u001b[31mred\u001b[0m", { item_id: "t" }),
        ],
        { has_more: true, latest_todo: ev(2, "tool_result", "todos", { item_type: "todo_list" }) },
      ),
    });
    expect(state.session?.title).toBe("a");
    expect(state.events.map((event) => event.text)).toEqual(["Hi there", "red"]);
    expect(state.hasOlder).toBe(true);
    expect(state.oldestSequence).toBe(5);
    expect(state.loaded).toBe(true);
    expect(state.error).toBeNull();
  });

  it("keeps live events that arrived before the page", () => {
    const state = run(
      { type: "events", events: [ev(9, "agent_output", "live")] },
      { type: "loaded", session: session("a"), page: page([ev(5, "user_input", "hi"), ev(9, "agent_output", "live")]) },
    );
    expect(state.events.map((event) => event.sequence)).toEqual([5, 9]);
  });

  it("appends live events and merges deltas into the item they extend", () => {
    const state = run(
      { type: "loaded", session: session("a"), page: page([ev(1, "agent_output", "a", { item_id: "m" })]) },
      { type: "events", events: [ev(2, "agent_output", "b", { item_id: "m" }), ev(3, "user_input", "x")] },
    );
    expect(state.events.map((event) => event.text)).toEqual(["ab", "x"]);
  });

  it("folds an older page ahead and tracks where the next one starts", () => {
    const state = run(
      { type: "loaded", session: session("a"), page: page([ev(10, "user_input", "new")], { has_more: true }) },
      { type: "older", page: page([ev(3, "user_input", "old"), ev(4, "agent_output", "mid")], { has_more: false }) },
    );
    expect(state.events.map((event) => event.sequence)).toEqual([3, 4, 10]);
    expect(state.hasOlder).toBe(false);
    expect(state.oldestSequence).toBe(3);
  });

  it("stops offering older pages when one comes back empty", () => {
    const state = run(
      { type: "loaded", session: session("a"), page: page([ev(10, "user_input", "new")], { has_more: true }) },
      { type: "older", page: page([]) },
    );
    expect(state.hasOlder).toBe(false);
    expect(state.events).toHaveLength(1);
  });

  it("replaces the session record from a push", () => {
    const state = run({ type: "session", session: session("renamed") });
    expect(state.session?.title).toBe("renamed");
  });

  it("orders pending-question snapshots, but a first frame replaces", () => {
    const newer = run(
      { type: "pendingQuestions", snapshot: snapshot(5, 2), replace: true },
      { type: "pendingQuestions", snapshot: snapshot(5, 1), replace: false },
    );
    expect(newer.pendingQuestions?.revision).toBe(2);
    const reconnected = streamReducer(newer, { type: "pendingQuestions", snapshot: snapshot(1, 0), replace: true });
    expect(reconnected.pendingQuestions?.as_of_sequence).toBe(1);
  });

  it("tracks the connection and errors, and resets", () => {
    const state = run({ type: "connection", connection: "open" }, { type: "error", message: "boom" });
    expect(state.connection).toBe("open");
    expect(state.error).toBe("boom");
    expect(streamReducer(state, { type: "reset" })).toEqual(INITIAL_STREAM_STATE);
  });
});
