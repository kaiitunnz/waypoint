import { describe, expect, it } from "vitest";

import { buildSections, searchSessions, visibleSessions } from "./sessionSections";
import type { SessionRecord } from "./types";

const session = (id: string, overrides: Partial<SessionRecord> = {}): SessionRecord =>
  ({
    id,
    title: id,
    backend: "claude_code",
    source: "managed",
    status: "idle",
    cwd: "/repo",
    last_event_at: "2026-11-25T10:00:00Z",
    ...overrides,
  }) as SessionRecord;

describe("visibleSessions", () => {
  it("hides the assistant and telemetry sources", () => {
    const kept = visibleSessions([
      session("a"),
      session("b", { source: "assistant" }),
      session("c", { source: "telemetry" }),
    ]);
    expect(kept.map((s) => s.id)).toEqual(["a"]);
  });
});

describe("buildSections", () => {
  it("splits pinned, live and exited, with pinned newest pin first and the rest newest first", () => {
    const sections = buildSections(
      [
        session("old", { last_event_at: "2026-11-25T09:00:00Z" }),
        session("new", { last_event_at: "2026-11-25T11:00:00Z" }),
        session("pin-a", { pinned_at: "2026-11-01T00:00:00Z" }),
        session("pin-b", { pinned_at: "2026-11-02T00:00:00Z" }),
        session("gone", { status: "exited" }),
      ],
      new Set(),
    );
    expect(sections.pinned.map((s) => s.id)).toEqual(["pin-b", "pin-a"]);
    expect(sections.live.map((s) => s.id)).toEqual(["new", "old"]);
    expect(sections.pinnedExited).toEqual([]);
    expect(sections.exited.map((s) => s.id)).toEqual(["gone"]);
  });

  it("keeps every exited session out of Pinned, the pinned ones heading Exited by newest pin", () => {
    const sections = buildSections(
      [
        session("early-pin", { pinned_at: "2026-11-01T00:00:00Z", status: "exited", last_event_at: "2026-11-25T12:00:00Z" }),
        session("late-pin", { pinned_at: "2026-11-02T00:00:00Z", status: "exited", last_event_at: "2026-11-25T08:00:00Z" }),
        session("live-pin", { pinned_at: "2026-11-03T00:00:00Z" }),
        session("gone", { status: "exited" }),
      ],
      new Set(),
    );
    expect(sections.pinned.map((s) => s.id)).toEqual(["live-pin"]);
    expect(sections.pinnedExited.map((s) => s.id)).toEqual(["late-pin", "early-pin"]);
    expect(sections.exited.map((s) => s.id)).toEqual(["gone"]);
  });

  it("leaves an unpinned session with an ask to its card but keeps a pinned one", () => {
    const sections = buildSections(
      [session("asking"), session("pinned-asking", { pinned_at: "2026-11-01T00:00:00Z" })],
      new Set(["asking", "pinned-asking"]),
    );
    expect(sections.live).toEqual([]);
    expect(sections.pinned.map((s) => s.id)).toEqual(["pinned-asking"]);
  });
});

describe("searchSessions", () => {
  const sessions = [
    session("a", { title: "Fix held race", branch: "fix/held", last_event_at: "2026-11-25T10:00:00Z" }),
    session("b", { title: "Paper figures", pinned_at: "2026-11-01T00:00:00Z" }),
    session("c", { title: "Fix kv sweep", last_event_at: "2026-11-25T11:00:00Z", status: "exited" }),
  ];

  it("uses v1's query syntax", () => {
    expect(searchSessions(sessions, "fix").rest.map((s) => s.id)).toEqual(["c", "a"]);
    expect(searchSessions(sessions, "title:paper").lead.map((s) => s.id)).toEqual(["b"]);
    expect(searchSessions(sessions, "branch:fix/held OR title:paper")).toMatchObject({
      lead: [{ id: "b" }],
      rest: [{ id: "a" }],
    });
    expect(searchSessions(sessions, "state:exited").rest.map((s) => s.id)).toEqual(["c"]);
    expect(searchSessions(sessions, "zzz")).toEqual({ lead: [], rest: [] });
  });

  it("matches everything for a blank query", () => {
    const result = searchSessions(sessions, "");
    expect(result.lead.length + result.rest.length).toBe(3);
  });
});
