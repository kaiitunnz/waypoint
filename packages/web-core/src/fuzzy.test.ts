import { describe, expect, it } from "vitest";

import { matchPath, rankItems, span } from "./fuzzy";

const names = (items: string[], query: string, options?: { prefixOnly?: boolean }) =>
  rankItems(items, query, (item) => item, undefined, options).map((r) => r.item);

describe("span", () => {
  it("lists consecutive indexes", () => {
    expect(span(3, 3)).toEqual([3, 4, 5]);
    expect(span(0, 0)).toEqual([]);
  });
});

describe("rankItems", () => {
  it("keeps every item in input order for an empty query", () => {
    const ranked = rankItems(["b", "a"], "", (item) => item);
    expect(ranked).toEqual([
      { item: "b", positions: [], descriptionPositions: [] },
      { item: "a", positions: [], descriptionPositions: [] },
    ]);
  });

  it("ranks exact, prefix, word-start, abbreviation, substring, then subsequence", () => {
    const items = ["my-project-file", "specpfile", "prune", "p-f", "xx-pf", "pf"];
    expect(names(items, "pf")).toEqual(["pf", "xx-pf", "p-f", "specpfile", "my-project-file"]);
  });

  it("highlights the matched characters", () => {
    const [hit] = rankItems(["frontend"], "fe", (item) => item);
    expect(hit.positions).toEqual([0, 5]);
    const [substring] = rankItems(["frontend"], "ont", (item) => item);
    expect(substring.positions).toEqual([2, 3, 4]);
  });

  it("prefers a prefix hit over a later word-start hit", () => {
    expect(names(["create-pr", "project"], "pr")).toEqual(["project", "create-pr"]);
  });

  it("prefers a hit spanning a whole segment within one name", () => {
    const [hit] = rankItems(["my-project-pr"], "pr", (item) => item);
    expect(hit.positions).toEqual([11, 12]);
  });

  it("penalises a late start so a bare name beats a namespaced one", () => {
    expect(names(["ns:review-pr", "review-pr"], "review-pr")).toEqual(["review-pr", "ns:review-pr"]);
    expect(names(["ns:review", "review-x"], "rev")).toEqual(["review-x", "ns:review"]);
  });

  it("matches case-insensitively for a lowercase query and case-sensitively otherwise", () => {
    expect(names(["HomeDir", "homedir"], "home")).toEqual(["HomeDir", "homedir"]);
    expect(names(["homedir", "HomeDir"], "Home")).toEqual(["HomeDir"]);
    expect(names(["homedir"], "$HOME")).toEqual([]);
  });

  it("treats camelCase humps as word starts", () => {
    const [hit] = rankItems(["fooBar"], "bar", (item) => item);
    expect(hit.positions).toEqual([3, 4, 5]);
  });

  it("ignores single-letter queries inside a name", () => {
    expect(names(["frontend"], "e")).toEqual([]);
    expect(names(["frontend", "echo"], "e")).toEqual(["echo"]);
  });

  it("limits to exact and prefix hits with prefixOnly", () => {
    const items = ["review", "pre-review", "reviewer"];
    expect(names(items, "review", { prefixOnly: true })).toEqual(["review", "reviewer"]);
    expect(names(items, "view", { prefixOnly: true })).toEqual([]);
  });

  it("keeps input order for ties", () => {
    expect(names(["b-x", "a-x", "c-x"], "x")).toEqual(["b-x", "a-x", "c-x"]);
  });

  describe("descriptions", () => {
    const items = [
      { name: "alpha", description: "Deploy to staging" },
      { name: "beta", description: "Runs the tests" },
      { name: "gamma" },
    ];
    const rank = (query: string, options?: { prefixOnly?: boolean }) =>
      rankItems(items, query, (i) => i.name, (i) => i.description, options);

    it("falls back to the description for queries of three or more characters", () => {
      const ranked = rank("tes");
      expect(ranked.map((r) => r.item.name)).toEqual(["beta"]);
      expect(ranked[0].positions).toEqual([]);
      expect(ranked[0].descriptionPositions).toEqual([9, 10, 11]);
    });

    it("matches a description prefix or word start but not an inner substring", () => {
      expect(rank("dep").map((r) => r.item.name)).toEqual(["alpha"]);
      expect(rank("sta").map((r) => r.item.name)).toEqual(["alpha"]);
      expect(rank("ploy")).toEqual([]);
    });

    it("skips descriptions for short queries and with prefixOnly", () => {
      expect(rank("de")).toEqual([]);
      expect(rank("dep", { prefixOnly: true })).toEqual([]);
    });

    it("ranks name hits above description hits", () => {
      const ranked = rankItems(
        [{ name: "zzz", d: "testing" }, { name: "test", d: "" }],
        "test",
        (i) => i.name,
        (i) => i.d,
      );
      expect(ranked.map((r) => r.item.name)).toEqual(["test", "zzz"]);
    });
  });
});

describe("matchPath", () => {
  it("highlights the last substring occurrence for a query without a slash", () => {
    expect(matchPath("src/lib/fuzzy.ts", "fuzzy")).toEqual([8, 9, 10, 11, 12]);
    expect(matchPath("a/ab/ab.ts", "ab")).toEqual([5, 6]);
  });

  it("falls back to a subsequence match anywhere in the path", () => {
    expect(matchPath("src/lib/fuzzy.ts", "sfz")).toEqual([0, 8, 10]);
  });

  it("returns null when the query does not match", () => {
    expect(matchPath("src/lib/fuzzy.ts", "zzq")).toBeNull();
  });

  it("is case-insensitive", () => {
    expect(matchPath("README.md", "readme")).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it("binds each folder segment of a slash query to the earliest match", () => {
    expect(matchPath("frontend/src/lib/fuzzy.ts", "src/fuzzy")).toEqual([9, 10, 11, 17, 18, 19, 20, 21]);
    expect(matchPath("frontend/src/lib/fuzzy.ts", "fr/lib/fz")).toEqual([0, 1, 13, 14, 15, 17, 19]);
  });

  it("returns null when a folder segment or the file name does not match", () => {
    expect(matchPath("frontend/src/fuzzy.ts", "nope/fuzzy")).toBeNull();
    expect(matchPath("frontend/src/fuzzy.ts", "src/nope")).toBeNull();
  });

  it("returns null for a query made only of slashes", () => {
    expect(matchPath("a/b", "/")).toBeNull();
  });
});
