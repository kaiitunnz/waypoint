import type { Root } from "hast";
import { describe, expect, it } from "vitest";

import {
  COMMON_REHYPE_PLUGINS,
  orderedListMarkerDigits,
  rehypeAppendReserve,
} from "./markdown";

describe("orderedListMarkerDigits", () => {
  it("counts the digits of the last marker", () => {
    expect(orderedListMarkerDigits(undefined, 1)).toBe(1);
    expect(orderedListMarkerDigits(1, 9)).toBe(1);
    expect(orderedListMarkerDigits(1, 10)).toBe(2);
    expect(orderedListMarkerDigits(95, 10)).toBe(3);
    expect(orderedListMarkerDigits(0, 100)).toBe(2);
  });

  it("treats an empty list as a single item", () => {
    expect(orderedListMarkerDigits(undefined, 0)).toBe(1);
    expect(orderedListMarkerDigits(10, 0)).toBe(2);
  });
});

describe("math fence guard", () => {
  type Element = {
    type: "element";
    tagName: string;
    properties: { className?: unknown };
    children: Element[];
  };
  const code = (className: unknown): Element => ({
    type: "element",
    tagName: "code",
    properties: { className },
    children: [],
  });
  const pre = (child: Element): Element => ({
    type: "element",
    tagName: "pre",
    properties: {},
    children: [child],
  });
  const runGuard = (child: Element, wrap: (child: Element) => Element = pre) => {
    const guard = COMMON_REHYPE_PLUGINS[0] as () => (tree: unknown) => void;
    const root = { type: "root", children: [wrap(child)] };
    guard()(root);
    return child.properties.className;
  };

  it("drops a bare language-math class so a math fence stays code", () => {
    expect(runGuard(code(["language-math", "other"]))).toEqual(["other"]);
  });

  it("keeps the class on real math", () => {
    expect(runGuard(code(["language-math", "math-display"]))).toEqual(["language-math", "math-display"]);
    expect(runGuard(code(["language-math", "math-inline"]))).toEqual(["language-math", "math-inline"]);
  });

  it("leaves other code blocks, non-array classes and non-pre parents alone", () => {
    expect(runGuard(code(["language-js"]))).toEqual(["language-js"]);
    expect(runGuard(code("language-math"))).toBe("language-math");
    expect(runGuard(code(["language-math"]), (child) => ({ ...pre(child), tagName: "div" }))).toEqual([
      "language-math",
    ]);
  });
});

describe("rehypeAppendReserve", () => {
  const el = (tagName: string, children: unknown[] = [], properties: Record<string, unknown> = {}) => ({
    type: "element" as const,
    tagName,
    properties,
    children,
  });
  const text = (value: string) => ({ type: "text" as const, value });
  const run = (children: unknown[]) => {
    const tree = { type: "root" as const, children } as unknown as Root;
    rehypeAppendReserve()(tree);
    return tree;
  };
  type Node = { type: string; tagName?: string; value?: string; properties?: { className?: string[] }; children?: Node[] };
  const tags = (node: Node): unknown[] =>
    (node.children ?? []).map((c) => {
      if (c.type === "text") return c.value;
      return [c.tagName, c.properties?.className?.[0] ?? null, ...tags(c)].filter((v) => v !== null);
    });

  it("puts the spacer at the end of the last paragraph", () => {
    const tree = run([el("p", [text("one")]), el("p", [text("two")])]);
    expect(tags(tree)).toEqual([["p", "one"], ["p", "two", ["span", "rsv"]]]);
  });

  it("descends into the last item of the last list", () => {
    const tree = run([el("p", [text("x")]), el("ul", [text("\n"), el("li", [text("a")]), text("\n"), el("li", [text("b")])])]);
    const ul = tree.children[1] as unknown as { children: { children: unknown[] }[] };
    const lastLi = ul.children[ul.children.length - 1];
    expect(JSON.stringify(lastLi.children)).toContain("rsv");
    expect(JSON.stringify(tree.children[0])).not.toContain("rsv");
  });

  it("follows a loose list item into its last paragraph", () => {
    const tree = run([el("ol", [el("li", [el("p", [text("a")])])])]);
    expect(tags(tree)).toEqual([["ol", ["li", ["p", "a", ["span", "rsv"]]]]]);
  });

  it("goes after inline elements that end the block", () => {
    const tree = run([el("p", [text("see "), el("code", [text("x")])])]);
    expect(tags(tree)).toEqual([["p", "see ", ["code", "x"], ["span", "rsv"]]]);
  });

  it("adds a row when the message ends in a code block or table", () => {
    expect(tags(run([el("p", [text("a")]), el("pre", [el("code", [text("x")])])]))).toEqual([
      ["p", "a"],
      ["pre", ["code", "x"]],
      ["div", "rsv-row"],
    ]);
    expect(tags(run([el("table")])).at(-1)).toEqual(["div", "rsv-row"]);
  });

  it("adds a row for an empty message", () => {
    expect(tags(run([]))).toEqual([["div", "rsv-row"]]);
  });
});
