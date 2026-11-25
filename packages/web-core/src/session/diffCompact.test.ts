import { describe, expect, it } from "vitest";

import { buildPlainDiffLines } from "../highlight";
import { compactDiff } from "./diffCompact";

const DIFF = [
  "--- a/f.py",
  "+++ b/f.py",
  "@@ -1,12 +1,12 @@",
  " c1",
  " c2",
  " c3",
  " c4",
  "-old",
  "+new",
  " c5",
  " c6",
  " c7",
  " c8",
  "+tail",
].join("\n");

const kinds = (diff: string, options = {}) =>
  compactDiff(buildPlainDiffLines(diff), options).map((row) =>
    row.kind === "line" ? `${row.line.kind}:${row.line.tokens[0].text}` : `${row.kind}:${row.count}`,
  );

describe("compactDiff", () => {
  it("keeps the changes with one line of context and folds the rest", () => {
    expect(kinds(DIFF)).toEqual(["fold:3", "context:c4", "del:old", "add:new", "context:c5", "fold:2", "context:c8", "add:tail"]);
  });

  it("drops file and hunk headers", () => {
    expect(kinds(DIFF).some((row) => row.startsWith("meta") || row.startsWith("hunk"))).toBe(false);
  });

  it("keeps a lone unchanged line instead of folding it", () => {
    const diff = "@@ -1,5 +1,5 @@\n-a\n+b\n c\n d\n-e\n+f";
    expect(kinds(diff)).toEqual(["del:a", "add:b", "context:c", "context:d", "del:e", "add:f"]);
    const gap = "@@ -1,5 +1,5 @@\n-a\n+b\n c\n d\n e\n-f\n+g";
    expect(kinds(gap)).toEqual(["del:a", "add:b", "context:c", "context:d", "context:e", "del:f", "add:g"]);
  });

  it("caps the rows and counts the rest", () => {
    expect(kinds(DIFF, { maxRows: 3 })).toEqual(["fold:3", "context:c4", "del:old", "more:6"]);
  });

  it("honours the context width", () => {
    expect(kinds(DIFF, { context: 0 })).toEqual(["fold:4", "del:old", "add:new", "fold:4", "add:tail"]);
  });

  it("returns nothing for an empty diff", () => {
    expect(kinds("")).toEqual([]);
  });
});
