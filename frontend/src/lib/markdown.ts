import type { Root } from "hast";
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

// throwOnError renders KaTeX's own error in place so malformed streamed TeX
// can't crash a message; trust and maxExpand bound untrusted input; errorColor
// is a token so the error resolves per theme.
export const COMMON_REHYPE_PLUGINS: PluggableList = [
  rehypePreserveMathFences,
  [
    rehypeKatex,
    { throwOnError: false, trust: false, maxExpand: 1000, errorColor: "var(--danger)" },
  ],
];
