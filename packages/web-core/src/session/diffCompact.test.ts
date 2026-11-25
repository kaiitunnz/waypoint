import { describe, expect, it } from "vitest";

import { buildPlainDiffLines } from "../highlight";
import { compactDiff, fullDiff } from "./diffCompact";

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

const kinds = (diff: string, maxLines?: number) =>
  compactDiff(buildPlainDiffLines(diff), maxLines).map((row) =>
    row.kind === "line" ? `${row.line.kind}:${row.line.tokens[0].text}` : `${row.kind}:${row.count}`,
  );

describe("compactDiff", () => {
  it("keeps the changes and folds only what lies between them", () => {
    expect(kinds(DIFF)).toEqual(["del:old", "add:new", "fold:4", "add:tail"]);
  });

  it("drops file and hunk headers", () => {
    expect(kinds(DIFF).some((row) => row.startsWith("meta") || row.startsWith("hunk"))).toBe(false);
  });

  it("keeps a lone unchanged line between changes instead of folding it", () => {
    expect(kinds("@@ -1,5 +1,5 @@\n-a\n+b\n c\n-d\n+e")).toEqual(["del:a", "add:b", "context:c", "del:d", "add:e"]);
  });

  it("caps the diff lines, dropping a fold that would end the list", () => {
    expect(kinds(DIFF, 2)).toEqual(["del:old", "add:new"]);
    expect(kinds(DIFF, 3)).toEqual(["del:old", "add:new", "fold:4", "add:tail"]);
  });

  it("returns nothing for an empty diff", () => {
    expect(kinds("")).toEqual([]);
  });
});

describe("fullDiff", () => {
  it("keeps every line, context included, and drops the headers", () => {
    const rows = fullDiff(buildPlainDiffLines(DIFF));
    expect(rows).toHaveLength(11);
    expect(rows.every((row) => row.kind === "line")).toBe(true);
  });
});
