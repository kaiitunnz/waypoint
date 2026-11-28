import { describe, expect, it } from "vitest";

import { toolFamilyOf } from "./toolFamily";

describe("toolFamilyOf", () => {
  it.each([
    ["Bash", "bash"],
    ["Monitor", "bash"],
    ["Read", "read"],
    ["Grep", "read"],
    ["Glob", "read"],
    ["WebFetch", "read"],
    ["WebSearch", "read"],
    ["Edit", "edit"],
    ["MultiEdit", "edit"],
    ["NotebookEdit", "edit"],
    ["Write", "edit"],
    ["Agent", "agent"],
    ["Task", "agent"],
    ["spawnAgent", "agent"],
    ["Subagent", "agent"],
    ["Wait", "agent"],
    ["closeAgent", "agent"],
    ["TodoWrite", "agent"],
    ["Skill", "skill"],
    ["mcp__notes__ping", null],
    ["notes:ping", null],
    ["ToolSearch", null],
    [null, null],
  ])("puts %s in its family", (tool, family) => {
    expect(toolFamilyOf(tool)).toBe(family);
  });
});
