import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import {
  commandForEvent,
  commandOutputForEvent,
  fallbackDiffPreviewForFileEdit,
  isAgentToolName,
  isFileEditToolName,
  previewForToolEvent,
  toolBadgeFor,
  toolDisplayName,
  toolNameParts,
  toolPairAttachments,
} from "./toolPresentation";

const toolCall = (tool: string, input: Record<string, unknown>, text = "") =>
  ev(1, "tool_call", text, { tool_name: tool, payload: { input } });

describe("toolBadgeFor", () => {
  it("is neutral for a missing name", () => {
    expect(toolBadgeFor(null)).toMatchObject({ glyph: "→", variant: "default" });
  });

  it("badges known tools with a fixed glyph", () => {
    expect(toolBadgeFor("Bash").glyph).toBe("›_");
    expect(toolBadgeFor("Edit").glyph).toBe("✎");
    expect(toolBadgeFor("Read").glyph).toBe("▤");
    expect(toolBadgeFor("Skill").glyph).toBe("❖");
  });

  it("shortens MCP and plugin tool names", () => {
    expect(toolBadgeFor("mcp__lumid__xp_ask")).toEqual({ glyph: "⧉", variant: "mcp", label: "lumid · xp_ask" });
    expect(toolBadgeFor("server:tool").variant).toBe("mcp");
    expect(toolBadgeFor("Bash").variant).not.toBe("mcp");
  });

  it("leaves unlisted tools neutral", () => {
    expect(toolBadgeFor("ScheduleWakeup")).toEqual({ glyph: "ƒ", variant: "default", label: "ScheduleWakeup" });
  });
});

describe("commandForEvent", () => {
  it("reads a Claude command from the tool input and a Codex command from the item", () => {
    const claude = ev(1, "tool_call", "", { tool_name: "Bash", payload: { input: { command: "ls -la" } } });
    const codex = ev(2, "tool_call", "", { tool_name: "Bash", payload: { item: { command: "/bin/bash -lc \"npm run lint\"" } } });
    expect(commandForEvent(claude)).toBe("ls -la");
    expect(commandForEvent(codex)).toBe("npm run lint");
    expect(commandForEvent(ev(3, "tool_call", ""))).toBeNull();
  });

  it("reads a Codex command's output from the item, else the result text", () => {
    const codex = ev(1, "tool_result", "$ ls\nout", { payload: { item: { aggregatedOutput: "out" } } });
    expect(commandOutputForEvent(codex)).toBe("out");
    expect(commandOutputForEvent(ev(2, "tool_result", "plain"))).toBe("plain");
  });
});

describe("tool name classes", () => {
  it("recognises file edits and subagent tools", () => {
    expect(isFileEditToolName("Edit")).toBe(true);
    expect(isFileEditToolName("Write")).toBe(true);
    expect(isFileEditToolName("Read")).toBe(false);
    expect(isFileEditToolName(null)).toBe(false);
    expect(isAgentToolName("spawnAgent")).toBe(true);
    expect(isAgentToolName("Bash")).toBe(false);
  });
});

describe("previewForToolEvent", () => {
  it("shows a command on one line", () => {
    expect(previewForToolEvent(toolCall("Bash", { command: "npm run\n  lint" }), "Bash")).toBe("npm run lint");
  });

  it("shows the path of a file edit", () => {
    expect(previewForToolEvent(toolCall("Edit", { file_path: "/a/b.py" }), "Edit")).toBe("/a/b.py");
  });

  it("shows a read's line range", () => {
    expect(previewForToolEvent(toolCall("Read", { file_path: "/a.py", offset: 10, limit: 20 }), "Read")).toBe(
      "/a.py · 10..30",
    );
    expect(previewForToolEvent(toolCall("Read", { file_path: "/a.py" }), "Read")).toBe("/a.py");
  });

  it("shows a grep pattern with its path", () => {
    expect(previewForToolEvent(toolCall("Grep", { pattern: "foo", path: "src" }), "Grep")).toBe("foo  ·  src");
    expect(previewForToolEvent(toolCall("Glob", { pattern: "**/*.ts" }), "Glob")).toBe("**/*.ts");
  });

  it("counts todos", () => {
    expect(previewForToolEvent(toolCall("TodoWrite", { todos: [{}, {}] }), "Todo")).toBe("2 todos");
    expect(previewForToolEvent(toolCall("TodoWrite", { todos: [{}] }), "Todo")).toBe("1 todo");
  });

  it("joins the arguments of other tools", () => {
    expect(previewForToolEvent(toolCall("Skill", { skill: "loop-dev", args: "x" }), "Skill")).toBe(
      "skill: loop-dev, args: x",
    );
  });

  it("falls back to the event text for results", () => {
    const result = ev(2, "tool_result", "12 passed\nin 1.8s", { tool_name: "Bash" });
    expect(previewForToolEvent(result, "Bash")).toBe("12 passed · 2 lines");
  });
});

describe("toolNameParts", () => {
  it("splits an MCP or plugin name into server and tool", () => {
    expect(toolNameParts("mcp__local-notes__lookup_note")).toEqual({ server: "local-notes", tool: "lookup_note" });
    expect(toolNameParts("mcp__claude_ai_Drive__search__files")).toEqual({ server: "claude_ai_Drive", tool: "search__files" });
    expect(toolNameParts("docs:search")).toEqual({ server: "docs", tool: "search" });
  });

  it("displays one name for a server and its tool", () => {
    expect(toolDisplayName("mcp__claude_ai_Drive__search__files")).toBe("claude_ai_Drive · search__files");
    expect(toolDisplayName("docs:search")).toBe("docs · search");
    expect(toolDisplayName("ListAgents")).toBe("ListAgents");
    expect(toolBadgeFor("mcp__thing").label).toBe("mcp__thing");
  });

  it("leaves any other name whole", () => {
    for (const name of ["ListAgents", "docs_search", "mcp__thing"]) {
      expect(toolNameParts(name)).toEqual({ server: null, tool: name });
    }
  });
});

describe("previewForToolEvent without structured input", () => {
  const preview = (text: string, label = "Bash") => previewForToolEvent(ev(1, "tool_result", text, {}), label);

  it("drops a leading tool-name line", () => {
    expect(preview("Bash\nls -la")).toBe("ls -la");
  });

  it("says so when there is nothing", () => {
    expect(preview("  \n ")).toBe("No output");
  });

  it("flattens a JSON body to key: value pairs", () => {
    expect(preview('{\n "a": 1,\n "b": "x"\n}')).toBe("a: 1, b: x");
  });

  it("truncates a long first line", () => {
    const text = preview("x".repeat(300));
    expect(text.endsWith("…")).toBe(true);
    expect(text.length).toBe(160);
  });
});

describe("fallbackDiffPreviewForFileEdit", () => {
  it("names the edited file when no diff came with the event", () => {
    const preview = fallbackDiffPreviewForFileEdit([toolCall("Edit", { file_path: "/a.py" })], "proposed");
    expect(preview?.phase).toBe("proposed");
    expect(preview?.files[0]).toMatchObject({ path: "/a.py", diff: "" });
    expect(preview?.files[0].unavailableReason).toContain("not included");
  });

  it("is null for non-edit tools and missing paths", () => {
    expect(fallbackDiffPreviewForFileEdit([toolCall("Bash", { command: "ls" })], "applied")).toBeNull();
    expect(fallbackDiffPreviewForFileEdit([toolCall("Edit", {})], "applied")).toBeNull();
    expect(fallbackDiffPreviewForFileEdit([null, undefined], "applied")).toBeNull();
  });
});

describe("toolPairAttachments", () => {
  const spec = (id: string) => ({ id, filename: `${id}.txt`, kind: "file", size: 1 });

  it("lists a pair's attachments once", () => {
    const call = ev(1, "tool_call", "", { attachments: [spec("a")] });
    const result = ev(2, "tool_result", "", { attachments: [spec("a"), spec("b")] });
    const pair = { call, result, itemId: "i", ts: result.ts, sequence: 2 };
    expect(toolPairAttachments(pair).map((s) => s.id)).toEqual(["a", "b"]);
  });
});

describe("a subagent call's preview", () => {
  const input = { description: "Find callers", prompt: "List them" };

  it("reads the summary, and the input's description for events stored before it", () => {
    const summarized = ev(1, "tool_call", "", { tool_name: "Agent", payload: { input }, summary: "Find every caller" });
    expect(previewForToolEvent(summarized, "Agent")).toBe("Find every caller");
    expect(previewForToolEvent(toolCall("Agent", input), "Agent")).toBe("Find callers");
  });
});
