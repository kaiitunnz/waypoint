import type { Element, ElementContent, Root, RootContent } from "hast";
import rehypeKatex from "rehype-katex";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import type { PluggableList } from "unified";
import { visit } from "unist-util-visit";

export const COMMON_REMARK_PLUGINS: PluggableList = [
  remarkGfm,
  remarkBreaks,
  remarkMath,
];

// rehype-katex renders every `language-math` element, including a ```math fence
// that remark-math never parsed as math (real math also carries math-inline or
// math-display). Drop the bare class so such a fence stays a code block.
function rehypePreserveMathFences() {
  return (tree: Root) => {
    visit(tree, "element", (node, _index, parent) => {
      if (
        node.tagName !== "code" ||
        parent?.type !== "element" ||
        parent.tagName !== "pre" ||
        !Array.isArray(node.properties.className)
      ) {
        return;
      }
      const classes = node.properties.className;
      if (
        classes.includes("language-math") &&
        !classes.includes("math-display") &&
        !classes.includes("math-inline")
      ) {
        node.properties.className = classes.filter((c) => c !== "language-math");
      }
    });
  };
}

// Digits in the widest marker an ordered list prints, which is its last number
// (`start + itemCount - 1`). The marker sits outside the list's content box, so
// the stylesheet needs this count to reserve room for it; CSS can't derive it.
export function orderedListMarkerDigits(
  start: number | undefined,
  itemCount: number,
): number {
  return String((start ?? 1) + Math.max(itemCount, 1) - 1).length;
}

// rehype-katex renders KaTeX's own error markup in place of throwing, so
// malformed streamed TeX can't crash a message. trust and maxExpand bound
// untrusted input; errorColor is a token so the error resolves per theme.
export const COMMON_REHYPE_PLUGINS: PluggableList = [
  rehypePreserveMathFences,
  [rehypeKatex, { trust: false, maxExpand: 1000, errorColor: "var(--danger)" }],
];

const INLINE_END_BLOCKS = new Set(["p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]);
const NESTING_BLOCKS = new Set(["ul", "ol", "blockquote"]);
const OPAQUE_BLOCKS = new Set(["pre", "table", "hr", "div", "details", "img"]);

const reserveSpan = (): Element => ({
  type: "element",
  tagName: "span",
  properties: { className: ["rsv"] },
  children: [],
});

function lastMeaningful(node: Root | Element): RootContent | ElementContent | null {
  for (let index = node.children.length - 1; index >= 0; index -= 1) {
    const child = node.children[index];
    if (child.type === "text" && !child.value.trim()) continue;
    if (child.type === "comment") continue;
    return child;
  }
  return null;
}

function appendReserve(node: Root | Element): boolean {
  const last = lastMeaningful(node);
  if (last?.type === "element") {
    if (OPAQUE_BLOCKS.has(last.tagName)) return false;
    if (INLINE_END_BLOCKS.has(last.tagName) || NESTING_BLOCKS.has(last.tagName)) return appendReserve(last);
  }
  // Text or an inline element ends the block.
  if (node.type !== "element" || !INLINE_END_BLOCKS.has(node.tagName)) return false;
  node.children.push(reserveSpan());
  return true;
}

// Appends an inline spacer to the end of the last text block, so a message
// bubble's absolutely positioned time and copy button have room on the last line.
// When the message ends in a code block, table or rule there is no line to share,
// and a short empty row is added for the meta to sit on.
export function rehypeAppendReserve() {
  return (tree: Root) => {
    if (appendReserve(tree)) return;
    tree.children.push({
      type: "element",
      tagName: "div",
      properties: { className: ["rsv-row"] },
      children: [],
    });
  };
}
