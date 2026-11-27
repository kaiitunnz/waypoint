import { describe, expect, it } from "vitest";

import { matchesQuery, parseQuery } from "./search";

describe("parseQuery", () => {
  it("returns no groups for an empty or blank query", () => {
    expect(parseQuery("")).toEqual([]);
    expect(parseQuery("   ")).toEqual([]);
  });

  it("lowercases plain terms and ANDs them in one group", () => {
    expect(parseQuery("Foo BAR")).toEqual([
      [
        { field: undefined, isRegex: false, value: "foo", start: 0, end: 3 },
        { field: undefined, isRegex: false, value: "bar", start: 4, end: 7 },
      ],
    ]);
  });

  it("places each term in the query, its field included", () => {
    const query = "agent:codex  AND /home/me";
    expect(parseQuery(query)[0].map((t) => query.slice(t.start, t.end))).toEqual(["agent:codex", "/home/me"]);
  });

  it("splits OR into groups and ignores AND and repeated or leading ORs", () => {
    const groups = parseQuery("a AND b or c");
    expect(groups.map((g) => g.map((t) => t.value))).toEqual([["a", "b"], ["c"]]);
    expect(parseQuery("OR a OR OR b").map((g) => g.map((t) => t.value))).toEqual([["a"], ["b"]]);
    expect(parseQuery("a OR")).toHaveLength(1);
  });

  it("treats OR and AND as plain words when they carry a field", () => {
    expect(parseQuery("title:or")[0][0]).toMatchObject({ field: "title", value: "or" });
  });

  it("parses field terms, lowercasing the field name", () => {
    expect(parseQuery("Title:bug")[0][0]).toMatchObject({ field: "title", value: "bug" });
  });

  it.each([
    ["repo", "repo_name"],
    ["dir", "cwd"],
    ["directory", "cwd"],
    ["path", "cwd"],
    ["app", "backend"],
    ["agent", "backend"],
    ["state", "search_status"],
    ["status", "search_status"],
  ])("aliases the %s field to %s", (alias, field) => {
    expect(parseQuery(`${alias}:x`)[0][0].field).toBe(field);
  });

  it("keeps quoted phrases together and strips the quotes", () => {
    expect(parseQuery(`title:"Fix the Bug" 'two words'`)[0].map((t) => [t.field, t.value])).toEqual([
      ["title", "fix the bug"],
      [undefined, "two words"],
    ]);
  });

  it("matches every item on its own, whatever flags a /regex/ carries", () => {
    const sessions = [{ cwd: "/home/a" }, { cwd: "/home/b" }];
    for (const query of ["/home/g", "/home/y"]) {
      const parsed = parseQuery(query);
      expect(sessions.filter((s) => matchesQuery(s, parsed, ["cwd"]))).toHaveLength(2);
    }
  });

  it("compiles /regex/ terms case-insensitively", () => {
    const [term] = parseQuery("/b.g/")[0];
    expect(term.isRegex).toBe(true);
    expect((term.value as RegExp).flags).toBe("i");
    expect((term.value as RegExp).test("BUG")).toBe(true);
  });

  it("keeps explicit regex flags and adds i", () => {
    const [term] = parseQuery("/a/g")[0];
    expect((term.value as RegExp).flags).toContain("g");
    expect((term.value as RegExp).flags).toContain("i");
  });

  it("falls back to a lowercased literal for an invalid regex", () => {
    const [term] = parseQuery("/(unclosed/")[0];
    expect(term.isRegex).toBe(false);
    expect(term.value).toBe("/(unclosed/");
  });

  it("allows an escaped slash inside a regex", () => {
    const [term] = parseQuery("cwd:/a\\/b/")[0];
    expect(term.isRegex).toBe(true);
    expect((term.value as RegExp).test("a/b")).toBe(true);
  });
});

describe("matchesQuery", () => {
  const item = {
    title: "Fix login Bug",
    repo_name: "waypoint",
    cwd: "/home/me/waypoint",
    backend: "claude_code",
    status: "running",
  };
  const run = (query: string, target: object = item, fields = ["title", "cwd"]) =>
    matchesQuery(target, parseQuery(query), fields);

  it("matches everything for an empty query", () => {
    expect(run("")).toBe(true);
  });

  it("searches the default fields case-insensitively", () => {
    expect(run("login")).toBe(true);
    expect(run("WAYPOINT")).toBe(true);
    expect(run("nonexistent")).toBe(false);
  });

  it("does not search fields outside the defaults", () => {
    expect(run("claude_code")).toBe(false);
  });

  it("restricts a field term to that field", () => {
    expect(run("backend:claude")).toBe(true);
    expect(run("agent:claude")).toBe(true);
    expect(run("repo:waypoint")).toBe(true);
    expect(run("title:waypoint")).toBe(false);
  });

  it("requires every term in a group and any group to match", () => {
    expect(run("login bug")).toBe(true);
    expect(run("login nope")).toBe(false);
    expect(run("nope OR login")).toBe(true);
    expect(run("nope OR zip")).toBe(false);
  });

  it("matches regex terms against the string value", () => {
    expect(run("title:/log.n/")).toBe(true);
    expect(run("title:/^bug/")).toBe(false);
  });

  it("treats missing fields as non-matching", () => {
    expect(run("title:x", { cwd: "x" })).toBe(false);
    expect(run("zzz", { title: null, cwd: undefined })).toBe(false);
  });

  it("stringifies non-string field values", () => {
    expect(run("count:42", { count: 42 })).toBe(true);
  });

  describe("status", () => {
    it("matches any non-exited status through the active alias", () => {
      expect(run("state:active")).toBe(true);
      expect(run("status:running")).toBe(true);
      expect(run("status:active", { ...item, status: "idle" })).toBe(true);
    });

    it("does not count an exited session as active", () => {
      expect(run("state:active", { ...item, status: "exited" })).toBe(false);
      expect(run("state:exited", { ...item, status: "exited" })).toBe(true);
    });

    it("leaves items without a status untouched", () => {
      expect(run("state:active", { title: "x" })).toBe(false);
    });
  });
});
