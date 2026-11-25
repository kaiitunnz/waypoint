import { describe, expect, it } from "vitest";

import { renameTarget, revertTitle } from "./sessionActions";
import type { SessionRecord } from "./types";

const session = (overrides: Partial<SessionRecord> = {}): SessionRecord =>
  ({ id: "s1", title: "Old", status: "idle", ...overrides }) as SessionRecord;

describe("session rename", () => {
  it("reverts only while the optimistic title is still current", () => {
    expect(revertTitle(session({ title: "New" }), "New", "Old").title).toBe("Old");
    expect(revertTitle(session({ title: "Newer" }), "New", "Old").title).toBe("Newer");
  });

  it("keeps whatever else changed while the rename was in flight", () => {
    const moved = session({ title: "New", status: "idle", read_at: "2026-01-02T00:00:00Z" });
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
