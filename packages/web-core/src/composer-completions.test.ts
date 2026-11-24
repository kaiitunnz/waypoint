import { describe, expect, it } from "vitest";

import {
  SLASH_NEW_FALLBACK,
  commandLabel,
  matchControlCommand,
  parseWorkspacePathToken,
  rowDescription,
  rowDescriptionPositions,
  rowHint,
  rowKey,
  rowLabel,
  rowSection,
  wordRangeAt,
  type SuggestionRow,
  type WorkspacePathSuggestion,
} from "./composer-completions";
import type { CommandCompletion } from "./types";

const command = (overrides: Partial<CommandCompletion> = {}): CommandCompletion => ({
  ...SLASH_NEW_FALLBACK,
  id: "c1",
  name: "compact",
  description: "Compact the conversation",
  ...overrides,
});

const commandRow = (overrides: Partial<CommandCompletion> = {}): SuggestionRow => ({
  type: "command",
  command: command(overrides),
  positions: [1, 2],
  descriptionPositions: [0, 1],
});

const pathRow = (overrides: Partial<WorkspacePathSuggestion> = {}): SuggestionRow => ({
  type: "workspace_path",
  replacement: "./src/a.ts",
  description: "File",
  section: "dir",
  positions: [2],
  ...overrides,
});

describe("SLASH_NEW_FALLBACK", () => {
  it("is the local /new session-control command", () => {
    expect(commandLabel(SLASH_NEW_FALLBACK)).toBe("/new");
    expect(SLASH_NEW_FALLBACK.replacement).toBe("/new ");
    expect(SLASH_NEW_FALLBACK.dispatch).toBe("frontend_control");
  });
});

describe("suggestion rows", () => {
  it("labels a command with its trigger and name", () => {
    expect(commandLabel(command({ trigger: "$", name: "skill" }))).toBe("$skill");
    expect(rowLabel(commandRow())).toBe("/compact");
  });

  it("labels a path row with its replacement text", () => {
    expect(rowLabel(pathRow())).toBe("./src/a.ts");
  });

  it("keys commands by id and paths by replacement", () => {
    expect(rowKey(commandRow())).toBe("c1");
    expect(rowKey(pathRow())).toBe("./src/a.ts");
  });

  it("describes commands, and paths only in the directory listing", () => {
    expect(rowDescription(commandRow())).toBe("Compact the conversation");
    expect(rowDescription(commandRow({ description: undefined }))).toBeNull();
    expect(rowDescription(pathRow({ description: "Directory" }))).toBe("Directory");
    expect(rowDescription(pathRow({ section: "workspace" }))).toBeNull();
  });

  it("exposes description highlight positions for commands only", () => {
    expect(rowDescriptionPositions(commandRow())).toEqual([0, 1]);
    expect(rowDescriptionPositions(pathRow())).toEqual([]);
  });

  it("shows the argument hint for commands that have one", () => {
    expect(rowHint(commandRow({ argument_hint: "[focus]" }))).toBe("[focus]");
    expect(rowHint(commandRow())).toBeNull();
    expect(rowHint(pathRow())).toBeNull();
  });

  it("reports the section of a path row and null otherwise", () => {
    expect(rowSection(pathRow({ section: "workspace" }))).toBe("workspace");
    expect(rowSection(pathRow({ section: "dir" }))).toBe("dir");
    expect(rowSection(commandRow())).toBeNull();
    expect(rowSection(undefined)).toBeNull();
  });
});

describe("parseWorkspacePathToken", () => {
  it("splits a ./ token into a directory and a leaf", () => {
    expect(parseWorkspacePathToken("./")).toEqual({ dir: "", leaf: "" });
    expect(parseWorkspacePathToken("./rea")).toEqual({ dir: "", leaf: "rea" });
    expect(parseWorkspacePathToken("./src/lib/fu")).toEqual({ dir: "src/lib", leaf: "fu" });
    expect(parseWorkspacePathToken("./src/")).toEqual({ dir: "src", leaf: "" });
  });

  it("is null for tokens not rooted at ./", () => {
    expect(parseWorkspacePathToken("src/a")).toBeNull();
    expect(parseWorkspacePathToken("/etc/hosts")).toBeNull();
    expect(parseWorkspacePathToken("../a")).toBeNull();
    expect(parseWorkspacePathToken("")).toBeNull();
  });

  it("rejects any directory segment of . or ..", () => {
    expect(parseWorkspacePathToken("./../etc/x")).toBeNull();
    expect(parseWorkspacePathToken("./a/../b/c")).toBeNull();
    expect(parseWorkspacePathToken("./a/./b/c")).toBeNull();
  });

  it("allows a dotted leaf and keeps dot-prefixed directory names", () => {
    expect(parseWorkspacePathToken("./a/..")).toEqual({ dir: "a", leaf: ".." });
    expect(parseWorkspacePathToken("./.github/work")).toEqual({ dir: ".github", leaf: "work" });
  });

  it("tolerates doubled slashes in the directory", () => {
    expect(parseWorkspacePathToken("./a//b")).toEqual({ dir: "a/", leaf: "b" });
  });
});

describe("wordRangeAt", () => {
  const text = "say hello  world";

  it("returns the bounds of the word the caret sits in or touches", () => {
    expect(wordRangeAt(text, 6)).toEqual([4, 9]);
    expect(wordRangeAt(text, 4)).toEqual([4, 9]);
    expect(wordRangeAt(text, 9)).toEqual([4, 9]);
  });

  it("returns an empty range on whitespace between words", () => {
    expect(wordRangeAt(text, 10)).toEqual([10, 10]);
  });

  it("handles the start and end of the text", () => {
    expect(wordRangeAt(text, 0)).toEqual([0, 3]);
    expect(wordRangeAt(text, text.length)).toEqual([11, 16]);
    expect(wordRangeAt("", 0)).toEqual([0, 0]);
  });

  it("treats newlines and tabs as whitespace", () => {
    expect(wordRangeAt("a\nbc\td", 3)).toEqual([2, 4]);
  });
});

describe("matchControlCommand", () => {
  it("returns an empty argument string for the bare command", () => {
    expect(matchControlCommand("/new", "new")).toBe("");
  });

  it("returns the trimmed trailing argument", () => {
    expect(matchControlCommand("/new  fix the bug  ", "new")).toBe("fix the bug");
    expect(matchControlCommand("/new ", "new")).toBe("");
  });

  it("requires the separating space, so a longer command name is not matched", () => {
    expect(matchControlCommand("/news", "new")).toBeNull();
    expect(matchControlCommand("/new-thing x", "new")).toBeNull();
  });

  it("rejects other commands and plain text", () => {
    expect(matchControlCommand("/fork", "new")).toBeNull();
    expect(matchControlCommand("new", "new")).toBeNull();
    expect(matchControlCommand(" /new", "new")).toBeNull();
    expect(matchControlCommand("", "new")).toBeNull();
  });
});
