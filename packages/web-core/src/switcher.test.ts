import { describe, expect, it } from "vitest";

import { mergeSwitcherEntries, rankSessionsForSwitcher, stepSelection } from "./switcher";
import type { SessionRecord } from "./types";

const session = (id: string, title: string, overrides: Partial<SessionRecord> = {}): SessionRecord =>
  ({
    id,
    title,
    status: "idle",
    cwd: "/home/me/waypoint",
    repo_name: "waypoint",
    branch: "main",
    last_event_at: "2026-11-25T10:00:00Z",
    ...overrides,
  }) as SessionRecord;

const ids = (entries: ReturnType<typeof rankSessionsForSwitcher>) => entries.map((e) => e.session.id);

describe("rankSessionsForSwitcher", () => {
  const sessions = [
    session("live-old", "Heatmap keyboard access", { last_event_at: "2026-11-25T09:00:00Z" }),
    session("live-new", "Manager: waypoint", { last_event_at: "2026-11-25T11:00:00Z" }),
    session("pin", "Paper figures", { pinned_at: "2026-11-01T00:00:00Z" }),
    session("ask", "Fix held-message race", { last_event_at: "2026-11-25T10:30:00Z" }),
    session("gone", "Old sweep", { status: "exited" }),
    session("pin-gone", "Pinned and exited", { status: "exited", pinned_at: "2026-11-02T00:00:00Z" }),
  ];
  const asks = new Set(["ask", "pin"]);

  it("keeps the section order with a blank query, newest first inside a section", () => {
    const entries = rankSessionsForSwitcher(sessions, asks, "");
    expect(entries.map((e) => [e.session.id, e.section])).toEqual([
      ["ask", "needs"],
      ["pin", "needs"],
      ["live-new", "live"],
      ["live-old", "live"],
      ["pin-gone", "exited"],
      ["gone", "exited"],
    ]);
  });

  it("heads Exited with the pinned ones, newest pin first, ahead of newer unpinned ones", () => {
    const entries = rankSessionsForSwitcher(
      [
        session("newer", "Newer", { status: "exited", last_event_at: "2026-11-25T12:00:00Z" }),
        session("older", "Older", { status: "exited", last_event_at: "2026-11-25T11:00:00Z" }),
        session("early-pin", "Early pin", { status: "exited", pinned_at: "2026-11-01T00:00:00Z" }),
        session("late-pin", "Late pin", {
          status: "exited",
          pinned_at: "2026-11-02T00:00:00Z",
          last_event_at: "2026-11-25T01:00:00Z",
        }),
      ],
      new Set(),
      "",
    );
    expect(ids(entries)).toEqual(["late-pin", "early-pin", "newer", "older"]);
  });

  it("matches fuzzily and keeps results in their sections", () => {
    const entries = rankSessionsForSwitcher(sessions, asks, "fix");
    expect(ids(entries)).toEqual(["ask"]);
    expect(entries[0].positions).toEqual([0, 1, 2]);
    expect(ids(rankSessionsForSwitcher(sessions, asks, "pa"))).toEqual(["pin", "pin-gone"]);
    expect(rankSessionsForSwitcher(sessions, asks, "pa").map((e) => e.section)).toEqual(["needs", "exited"]);
    expect(ids(rankSessionsForSwitcher(sessions, asks, "hmka"))).toEqual(["live-old"]);
  });

  it("falls back to repo, branch and directory for a longer query", () => {
    const entries = rankSessionsForSwitcher(
      [session("a", "Alpha", { branch: "fix/held-race" }), session("b", "Beta")],
      new Set(),
      "held-race",
    );
    expect(ids(entries)).toEqual(["a"]);
  });

  it("returns nothing when nothing matches", () => {
    expect(rankSessionsForSwitcher(sessions, asks, "zzzzzz")).toEqual([]);
  });
});

describe("mergeSwitcherEntries", () => {
  it("appends server history as Exited, without repeating a session the list holds", () => {
    const ranked = rankSessionsForSwitcher([session("a", "Alpha")], new Set(), "");
    const merged = mergeSwitcherEntries(ranked, [session("a", "Alpha"), session("b", "Older")]);
    expect(merged.map((e) => [e.session.id, e.section])).toEqual([
      ["a", "live"],
      ["b", "exited"],
    ]);
    expect(merged[1].positions).toEqual([]);
  });
});

describe("stepSelection", () => {
  it("wraps at both ends and handles an empty list", () => {
    expect(stepSelection(0, -1, 3)).toBe(2);
    expect(stepSelection(2, 1, 3)).toBe(0);
    expect(stepSelection(1, 1, 3)).toBe(2);
    expect(stepSelection(0, 1, 0)).toBeNull();
  });
});
