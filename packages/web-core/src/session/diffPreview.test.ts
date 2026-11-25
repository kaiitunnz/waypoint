import { describe, expect, it } from "vitest";

import type { EventDiffPreviewFile } from "../events";
import { diffFilePathLabel, displayChangeType } from "./diffPreview";

const file = (extra: Partial<EventDiffPreviewFile>): EventDiffPreviewFile => ({
  path: "a.py",
  oldPath: null,
  changeType: "unknown",
  diff: "",
  additions: 0,
  deletions: 0,
  truncated: false,
  binary: false,
  unavailableReason: null,
  ...extra,
});

describe("displayChangeType", () => {
  it("trusts the backend, then the diff, then calls it an edit", () => {
    expect(displayChangeType(file({ changeType: "add" }))).toEqual({ className: "add", label: "add" });
    expect(displayChangeType(file({ diff: "--- /dev/null\n+++ b/a\n+x" })).label).toBe("add");
    expect(displayChangeType(file({}))).toEqual({ className: "update", label: "edit" });
  });

  it("reads additions, deletions, moves and updates off the diff headers", () => {
    const label = (diff: string) => displayChangeType(file({ diff })).className;
    expect(label("--- /dev/null\n+++ b/a.py\n+x")).toBe("add");
    expect(label("--- a/a.py\n+++ /dev/null\n-x")).toBe("delete");
    expect(label("--- a/a.py\n+++ b/b.py\n+x")).toBe("move");
    expect(label("--- a/a.py\n+++ b/a.py\n-x\n+y")).toBe("update");
    expect(label("--- a/a.py\t2026-01-01\n+++ b/a.py\t2026-01-02\n-x\n+y")).toBe("update");
  });
});

describe("diffFilePathLabel", () => {
  it("shows a move as old -> new", () => {
    expect(diffFilePathLabel(file({ path: "b.py", oldPath: "a.py" }))).toBe("a.py -> b.py");
    expect(diffFilePathLabel(file({ path: "b.py", oldPath: "b.py" }))).toBe("b.py");
  });

  it("reads relative to the working directory when the file is inside it", () => {
    expect(diffFilePathLabel(file({ path: "/repo/src/b.py" }), "/repo")).toBe("src/b.py");
    expect(diffFilePathLabel(file({ path: "/repo/b.py", oldPath: "/repo/a.py" }), "/repo")).toBe("a.py -> b.py");
    expect(diffFilePathLabel(file({ path: "/etc/b.py" }), "/repo")).toBe("/etc/b.py");
  });
});
