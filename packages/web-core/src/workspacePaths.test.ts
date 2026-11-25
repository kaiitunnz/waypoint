import { describe, expect, it } from "vitest";

import {
  findWorkspacePaths,
  formatCwdSegments,
  isWorkspacePathHref,
  relativeToCwd,
  relativizeInText,
  remarkLinkifyPaths,
} from "./workspacePaths";

describe("isWorkspacePathHref", () => {
  it.each(["/etc/hosts", "/a", "~/notes.md", "./src/a.ts", "../up/file"])("accepts %s", (href) => {
    expect(isWorkspacePathHref(href)).toBe(true);
  });

  it.each([
    "https://example.com/a",
    "mailto:me@example.com",
    "data:text/plain,hi",
    "file:///etc/hosts",
    "//cdn.example.com/x",
    "#anchor",
    "relative/path",
    "",
    null,
    undefined,
  ])("rejects %s", (href) => {
    expect(isWorkspacePathHref(href)).toBe(false);
  });
});

describe("findWorkspacePaths", () => {
  const values = (text: string) => findWorkspacePaths(text).map((s) => s.value);

  it("finds relative, home-relative and absolute paths", () => {
    expect(values("see ./a/b.ts, ../c, ~/d/e and /f/g/h")).toEqual(["./a/b.ts", "../c", "~/d/e", "/f/g/h"]);
  });

  it("requires two segments for an absolute path", () => {
    expect(values("open /tmp now")).toEqual([]);
    expect(values("open /tmp/x now")).toEqual(["/tmp/x"]);
  });

  it("does not start in the middle of a token", () => {
    expect(values("a/b/c")).toEqual([]);
    expect(values("word./x/y")).toEqual([]);
  });

  it("skips URLs", () => {
    expect(values("https://example.com/a/b and //cdn.example.com/x/y")).toEqual([]);
  });

  it("strips trailing sentence punctuation", () => {
    expect(values("edit ./a.ts.")).toEqual(["./a.ts"]);
    expect(values("is it /a/b?")).toEqual(["/a/b"]);
    expect(values("(see /a/b), then /c/d;")).toEqual(["/a/b", "/c/d"]);
  });

  it("stops at closing brackets, quotes and whitespace", () => {
    expect(values('"/a/b" `/c/d` [/e/f] {/g/h}')).toEqual(["/a/b", "/c/d", "/e/f", "/g/h"]);
  });

  it("reports offsets into the text", () => {
    expect(findWorkspacePaths("at ./a/b now")).toEqual([{ start: 3, end: 8, value: "./a/b" }]);
    expect(findWorkspacePaths("at ./a/b. now")[0].end).toBe(8);
  });

  it("is stateless across calls", () => {
    expect(values("./a/b")).toEqual(["./a/b"]);
    expect(values("./a/b")).toEqual(["./a/b"]);
  });
});

describe("remarkLinkifyPaths", () => {
  type Node = { type: string; value?: string; url?: string; children?: Node[]; data?: unknown };
  const run = (tree: Node): Node => {
    const transform = (remarkLinkifyPaths as unknown as () => (tree: Node) => void)();
    transform(tree);
    return tree;
  };
  const text = (value: string): Node => ({ type: "text", value });

  it("splits prose into text and link nodes", () => {
    const tree = run({ type: "paragraph", children: [text("open ./a/b and ~/c/d now")] });
    expect(tree.children).toEqual([
      text("open "),
      {
        type: "link",
        url: "./a/b",
        data: { hProperties: { "data-wp-bare": "true" } },
        children: [text("./a/b")],
      },
      text(" and "),
      {
        type: "link",
        url: "~/c/d",
        data: { hProperties: { "data-wp-bare": "true" } },
        children: [text("~/c/d")],
      },
      text(" now"),
    ]);
  });

  it("leaves text without paths as a single text node", () => {
    const tree = run({ type: "paragraph", children: [text("nothing here")] });
    expect(tree.children).toEqual([text("nothing here")]);
  });

  it("does not linkify inside existing links or non-text nodes", () => {
    const link: Node = { type: "link", url: "x", children: [text("./a/b")] };
    const code: Node = { type: "inlineCode", value: "./a/b" };
    const tree = run({ type: "paragraph", children: [link, code] });
    expect(tree.children).toEqual([
      { type: "link", url: "x", children: [text("./a/b")] },
      { type: "inlineCode", value: "./a/b" },
    ]);
  });

  it("recurses into nested containers", () => {
    const tree = run({
      type: "root",
      children: [{ type: "paragraph", children: [{ type: "strong", children: [text("./a/b")] }] }],
    });
    const strong = tree.children![0].children![0];
    expect(strong.children![0]).toMatchObject({ type: "link", url: "./a/b" });
  });
});

describe("formatCwdSegments", () => {
  it("returns a single empty segment for an empty path", () => {
    expect(formatCwdSegments("")).toEqual([""]);
  });

  it("keeps the leading slash on the first segment of an absolute path", () => {
    expect(formatCwdSegments("/home/me/project")).toEqual(["/home", "me", "project"]);
    expect(formatCwdSegments("/home")).toEqual(["/home"]);
  });

  it("returns an empty segment for the root, which has no name once slashes are trimmed", () => {
    expect(formatCwdSegments("/")).toEqual([""]);
    expect(formatCwdSegments("///")).toEqual([""]);
  });

  it("ignores trailing and repeated slashes", () => {
    expect(formatCwdSegments("/a//b/")).toEqual(["/a", "b"]);
  });

  it("splits relative and home-relative paths without a leading slash", () => {
    expect(formatCwdSegments("~/work/repo")).toEqual(["~", "work", "repo"]);
    expect(formatCwdSegments("a/b")).toEqual(["a", "b"]);
  });
});

describe("relativeToCwd", () => {
  it("drops the working directory from a path inside it", () => {
    expect(relativeToCwd("/repo/backend/src/runtime.py", "/repo")).toBe("backend/src/runtime.py");
    expect(relativeToCwd("/repo/hello.py", "/repo/")).toBe("hello.py");
    expect(relativeToCwd("/repo", "/repo")).toBe(".");
  });

  it("keeps a path outside the working directory", () => {
    expect(relativeToCwd("/etc/hosts", "/repo")).toBe("/etc/hosts");
    expect(relativeToCwd("/repository/a.py", "/repo")).toBe("/repository/a.py");
  });

  it("keeps a path that is already relative, and any path with no working directory", () => {
    expect(relativeToCwd("src/a.py", "/repo")).toBe("src/a.py");
    expect(relativeToCwd("/repo/a.py", "")).toBe("/repo/a.py");
    expect(relativeToCwd("/repo/a.py", null)).toBe("/repo/a.py");
    expect(relativeToCwd("/a.py", "/")).toBe("/a.py");
  });
});

describe("relativizeInText", () => {
  it("makes the paths inside the working directory relative, wherever they stand in the text", () => {
    expect(relativizeInText("release_all  ·  /repo/backend", "/repo")).toBe("release_all  ·  backend");
    expect(relativizeInText("path: /repo/a.py, other: /repo/b/c.py", "/repo")).toBe("path: a.py, other: b/c.py");
    expect(relativizeInText("/repo/src", "/repo/")).toBe("src");
    expect(relativizeInText("in /repo", "/repo")).toBe("in .");
  });

  it("leaves paths outside it and lookalikes alone", () => {
    expect(relativizeInText("/etc/hosts and /repository/a.py", "/repo")).toBe("/etc/hosts and /repository/a.py");
    expect(relativizeInText("/other/repo/a.py", "/repo")).toBe("/other/repo/a.py");
    expect(relativizeInText("/repo/a.py", "")).toBe("/repo/a.py");
  });
});
