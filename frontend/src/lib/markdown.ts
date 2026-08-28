import rehypeKatex from "rehype-katex";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import type { PluggableList } from "unified";

// Shared remark/rehype plugin arrays for every direct ReactMarkdown call site.
// Hoisted to module scope so their identity is stable across renders: combined
// with a memoized renderer, unchanged text skips the remark parse during
// streaming. remarkMath must precede any text-node walker (e.g. path linkify)
// so equation text becomes a math node before that walker sees it.
export const COMMON_REMARK_PLUGINS: PluggableList = [
  remarkGfm,
  remarkBreaks,
  remarkMath,
];

// Minimal hast shapes — a dependency-free walk avoids pulling unist-util-visit
// in as a direct dependency for a dozen lines.
interface HastElement {
  type: string;
  tagName?: string;
  properties?: { className?: unknown };
  children?: HastElement[];
}

// remark-math tags real math with `math-inline`/`math-display` alongside
// `language-math`; a ```` ```math ```` fence carries only `language-math`.
// rehype-katex renders any `language-math` element, so without this it would
// swallow that fence and turn agent-authored code into a formula. Strip the
// bare `language-math` from genuine fences before rehype-katex runs so they
// stay ordinary, copyable code blocks — this ticket only renders the
// dollar-delimited `$...$`/`$$...$$` syntax.
function rehypePreserveMathFences() {
  return (tree: HastElement) => {
    const walk = (node: HastElement, parent: HastElement | null) => {
      if (
        node.tagName === "code" &&
        parent?.tagName === "pre" &&
        Array.isArray(node.properties?.className)
      ) {
        const classes = node.properties.className as string[];
        if (
          classes.includes("language-math") &&
          !classes.includes("math-display") &&
          !classes.includes("math-inline")
        ) {
          node.properties.className = classes.filter(
            (name) => name !== "language-math",
          );
        }
      }
      node.children?.forEach((child) => walk(child, node));
    };
    walk(tree, null);
  };
}

// trust:false and the bounded maxExpand keep hostile TeX from producing HTML or
// unbounded expansion; throwOnError:false renders a local KaTeX error in place
// of throwing, so partial/streamed agent TeX can't break a whole message.
// errorColor is a design token so a parse error resolves to the theme's danger
// hue in both light and dark rather than KaTeX's fixed red.
export const COMMON_REHYPE_PLUGINS: PluggableList = [
  rehypePreserveMathFences,
  [
    rehypeKatex,
    {
      throwOnError: false,
      trust: false,
      maxExpand: 1000,
      errorColor: "var(--danger)",
    },
  ],
];
