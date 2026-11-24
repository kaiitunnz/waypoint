import { describe, expect, it } from "vitest";

import {
  COMMON_REHYPE_PLUGINS,
  COMMON_REMARK_PLUGINS,
  orderedListMarkerDigits,
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

describe("plugin lists", () => {
  it("enables GFM, hard line breaks and math on the markdown side", () => {
    expect(COMMON_REMARK_PLUGINS).toHaveLength(3);
  });

  it("renders KaTeX with untrusted-input bounds, after the math-fence guard", () => {
    expect(COMMON_REHYPE_PLUGINS).toHaveLength(2);
    const [, katex] = COMMON_REHYPE_PLUGINS as [unknown, [unknown, Record<string, unknown>]];
    expect(katex[1]).toEqual({ trust: false, maxExpand: 1000, errorColor: "var(--danger)" });
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
