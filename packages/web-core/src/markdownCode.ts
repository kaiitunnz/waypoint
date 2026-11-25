import { isValidElement, type ReactNode } from "react";

// Highlight only once the code stops changing: every streamed delta grows the
// text and resets the timer, so an in-flight message renders raw and a finalized
// one upgrades about one debounce later. Longer than the gap between streamed
// tokens, short enough to feel instant on loaded history.
export const HIGHLIGHT_DEBOUNCE_MS = 300;

// react-markdown renders fenced blocks as <pre><code>…</code></pre>; the `pre`
// override receives only the rendered tree, so the raw code is recovered by
// walking the children.
export function nodeToText(node: ReactNode): string {
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeToText).join("");
  if (isValidElement(node)) {
    return nodeToText((node.props as { children?: ReactNode }).children);
  }
  return "";
}

// react-markdown stamps the fence info onto the inner <code> as `language-<info>`.
export function fenceLanguage(children: ReactNode): string {
  const child = Array.isArray(children) ? children[0] : children;
  if (isValidElement(child)) {
    const className = (child.props as { className?: string }).className ?? "";
    const match = /(?:^|\s)language-(\S+)/.exec(className);
    if (match) return match[1];
  }
  return "";
}
