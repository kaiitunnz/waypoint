import { describe, expect, it } from "vitest";

import { markTargetConnected, resolveActiveTargetId } from "./launchTargets";
import type { LaunchTargetSummary } from "./types";

const target = (id: string, connected?: boolean): LaunchTargetSummary => ({
  id,
  name: id,
  kind: "ssh",
  supported_backends: ["claude_code"],
  default_backend: "claude_code",
  auth: "password",
  connected,
});

describe("markTargetConnected", () => {
  it("returns the same array when nothing changes", () => {
    const targets = [target("a", true)];
    expect(markTargetConnected(targets, "a", true)).toBe(targets);
    expect(markTargetConnected(targets, "missing", false)).toBe(targets);
  });

  it("updates only the named target", () => {
    const targets = [target("a", false), target("b", false)];
    const next = markTargetConnected(targets, "b", true);
    expect(next).not.toBe(targets);
    expect(next.map((t) => t.connected)).toEqual([false, true]);
    expect(targets[1].connected).toBe(false);
  });
});

describe("resolveActiveTargetId", () => {
  it("keeps a stored id the backend still lists", () => {
    expect(resolveActiveTargetId([target("a")], "a")).toBe("a");
  });

  it("falls back to the host itself", () => {
    expect(resolveActiveTargetId([target("a")], "gone")).toBe("");
    expect(resolveActiveTargetId([], "a")).toBe("");
  });
});
