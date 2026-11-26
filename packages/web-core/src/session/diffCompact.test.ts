import { describe, expect, it } from "vitest";

import { buildPlainDiffLines } from "../highlight";
import { compactDiff, fullDiff, reachableLines } from "./diffCompact";

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

  it("keeps the folded lines where the diff has them, and none between hunks", () => {
    const folds = (diff: string) =>
      compactDiff(buildPlainDiffLines(diff)).flatMap((row) => (row.kind === "fold" ? [row.lines?.map((line) => line.tokens[0].text) ?? null] : []));
    expect(folds(DIFF)).toEqual([["c5", "c6", "c7", "c8"]]);
    expect(folds("@@ -1,2 +1,2 @@\n-a\n+b\n@@ -20,2 +20,2 @@\n-c\n+d")).toEqual([null]);
  });

  it("counts the lines its folds carry as reachable, and not those cut off or between hunks", () => {
    const reachable = (diff: string, maxLines?: number) => reachableLines(compactDiff(buildPlainDiffLines(diff), maxLines));
    expect(reachable("@@ -1,5 +1,5 @@\n-a\n+b\n c1\n c2\n-d")).toBe(5);
    expect(reachable(DIFF)).toBe(7);
    expect(reachable(DIFF, 2)).toBe(2);
    expect(reachable("@@ -1,2 +1,2 @@\n-a\n+b\n@@ -20,2 +20,2 @@\n-c\n+d")).toBe(4);
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

  it("counts the lines between two hunks from their line numbers", () => {
    const diff = ["@@ -1,3 +1,3 @@", " a", "-b", "+B", " c", "@@ -100,3 +100,3 @@", " x", "-y", "+Y", " z"].join("\n");
    expect(kinds(diff)).toEqual(["del:b", "add:B", "fold:98", "del:y", "add:Y"]);
  });

  it("folds a hunk break even when only one line shows on each side", () => {
    const diff = ["@@ -1,2 +1,2 @@", "-a", "+A", " b", "@@ -9,2 +9,2 @@", " c", "-d", "+D"].join("\n");
    expect(kinds(diff)).toEqual(["del:a", "add:A", "fold:8", "del:d", "add:D"]);
  });

  it("leaves the count open when a hunk starts with a deletion and no context", () => {
    expect(kinds(["@@ -1,1 +1,1 @@", "-a", "+A", "@@ -50,1 +50,0 @@", "-z"].join("\n"))).toEqual(["del:a", "add:A", "fold:null", "del:z"]);
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
