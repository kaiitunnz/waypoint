import { describe, expect, it } from "vitest";

import type { WorkspaceGitFileStatus, WorkspaceGitStatus } from "../api";
import { changeLetter, summarizeChanges } from "./changesSummary";

const file = (path: string, indexStatus = " ", worktreeStatus = " ", untracked = false): WorkspaceGitFileStatus => ({
  path,
  oldPath: null,
  indexStatus,
  worktreeStatus,
  untracked,
});

const status = (files: WorkspaceGitFileStatus[], patch: Partial<WorkspaceGitStatus> = {}): WorkspaceGitStatus => ({
  enabled: true,
  branch: "fix/held-race",
  detached: false,
  files,
  ...patch,
});

describe("changeLetter", () => {
  it("reads untracked first, then the staged column, then the working tree", () => {
    expect(changeLetter(file("a", "?", "?", true))).toBe("U");
    expect(changeLetter(file("a", "A", "M"))).toBe("A");
    expect(changeLetter(file("a", " ", "D"))).toBe("D");
    expect(changeLetter(file("a", "R", " "))).toBe("R");
    expect(changeLetter(file("a", "C", " "))).toBe("A");
    expect(changeLetter(file("a", " ", "M"))).toBe("M");
    expect(changeLetter(file("a", "T", " "))).toBe("M");
  });
});

describe("summarizeChanges", () => {
  it("is nothing where git is off or the status could not be read", () => {
    expect(summarizeChanges(null)).toBeNull();
    expect(summarizeChanges(status([], { enabled: false }))).toBeNull();
  });

  it("counts a file once however many columns it changed in", () => {
    const summary = summarizeChanges(status([file("a.py", "M", "M"), file("a.py", " ", "M"), file("b.py", " ", " ", true)]));
    expect(summary?.count).toBe(2);
    expect(summary?.files).toEqual([
      { path: "a.py", letter: "M" },
      { path: "b.py", letter: "U" },
    ]);
  });

  it("carries the branch and whether HEAD is detached", () => {
    expect(summarizeChanges(status([]))).toMatchObject({ count: 0, branch: "fix/held-race", detached: false });
    expect(summarizeChanges(status([], { branch: null, detached: true }))).toMatchObject({ branch: null, detached: true });
  });
});
