import { describe, expect, it } from "vitest";

import { isExited, renameTarget, revertTitle, withTitle } from "./sessionActions";
import type { SessionRecord } from "./types";

const session = (overrides: Partial<SessionRecord> = {}): SessionRecord =>
  ({ id: "s1", title: "Old", status: "idle", ...overrides }) as SessionRecord;

describe("session rename", () => {
  it("applies an optimistic title without touching the rest", () => {
    const next = withTitle(session({ branch: "main" }), "New");
    expect(next).toMatchObject({ id: "s1", title: "New", branch: "main" });
  });

  it("reverts only while the optimistic title is still current", () => {
    const previous = session();
    const optimistic = withTitle(previous, "New");
    expect(revertTitle(optimistic, "New", previous.title).title).toBe("Old");
    const superseded = withTitle(previous, "Newer");
    expect(revertTitle(superseded, "New", previous.title).title).toBe("Newer");
  });

  it("keeps whatever else changed while the rename was in flight", () => {
    const optimistic = withTitle(session({ status: "running" }), "New");
    const moved = { ...optimistic, status: "idle" as const, read_at: "2026-01-02T00:00:00Z" };
    expect(revertTitle(moved, "New", "Old")).toMatchObject({
      title: "Old",
      status: "idle",
      read_at: "2026-01-02T00:00:00Z",
    });
  });

  it("sends a trimmed title and nothing for a blank or unchanged one", () => {
    expect(renameTarget(session(), "  Fresh  ")).toBe("Fresh");
    expect(renameTarget(session(), "   ")).toBeNull();
    expect(renameTarget(session(), "Old")).toBeNull();
    expect(renameTarget(session(), " Old ")).toBeNull();
  });
});

describe("isExited", () => {
  it("reads the status", () => {
    expect(isExited(session({ status: "exited" }))).toBe(true);
    expect(isExited(session({ status: "running" }))).toBe(false);
  });
});
