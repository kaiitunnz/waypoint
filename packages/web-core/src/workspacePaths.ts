import type { Plugin } from "unified";
import type { Node } from "unist";

// Filesystem paths we linkify in prose: explicit-relative (./a, ../a),
// home-relative (~/a), or absolute with at least two segments (/a/b…). The
// leading lookbehind keeps us from starting mid-token — so the "/b" inside
// "a/b" and the "//" of a protocol-relative URL are both skipped.
const WORKSPACE_PATH_RE =
  /(?<![\w~/.])(?:\.\.?\/[^\s)\]}>"'`,;]+(?:\/[^\s)\]}>"'`,;]+)*|~\/[^\s)\]}>"'`,;]+|\/[^\s/)\]}>"'`,;]+(?:\/[^\s)\]}>"'`,;]+)+)/g;

export function isWorkspacePathHref(href: string | undefined | null): boolean {
  if (!href) return false;
  if (href.startsWith("#")) return false;
  if (href.startsWith("//")) return false; // protocol-relative URL
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(href)) return false; // http:, mailto:, data:, …
  return (
    href.startsWith("/") ||
    href.startsWith("~/") ||
    href.startsWith("./") ||
    href.startsWith("../")
  );
}

export interface WorkspacePathSpan {
  start: number;
  end: number;
  value: string;
}

export function findWorkspacePaths(text: string): WorkspacePathSpan[] {
  const spans: WorkspacePathSpan[] = [];
  const re = new RegExp(WORKSPACE_PATH_RE.source, "g");
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    // Trailing sentence punctuation almost never belongs to a filename.
    const value = match[0].replace(/[.,;:!?]+$/, "");
    if (!value) continue;
    spans.push({ start: match.index, end: match.index + value.length, value });
  }
  return spans;
}

interface MdastNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdastNode[];
  data?: { hProperties?: Record<string, string> };
}

// Linkify bare filesystem paths in prose so the transcript can route a click to
// the workspace panel. Operating on the mdast (not the raw string) is what lets
// us leave code spans, fenced code, and already-linked text untouched.
export const remarkLinkifyPaths: Plugin<[], Node> = () => (tree) => {
  linkify(tree as unknown as MdastNode);
};

function linkify(node: MdastNode): void {
  if (!node.children || node.children.length === 0) return;
  if (node.type === "link") return; // never linkify inside an existing link
  const next: MdastNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && typeof child.value === "string") {
      next.push(...splitTextNode(child.value));
    } else {
      linkify(child);
      next.push(child);
    }
  }
  node.children = next;
}

function splitTextNode(text: string): MdastNode[] {
  const spans = findWorkspacePaths(text);
  if (spans.length === 0) return [{ type: "text", value: text }];
  const out: MdastNode[] = [];
  let cursor = 0;
  for (const span of spans) {
    if (span.start > cursor) {
      out.push({ type: "text", value: text.slice(cursor, span.start) });
    }
    out.push({
      type: "link",
      url: span.value,
      data: { hProperties: { "data-wp-bare": "true" } },
      children: [{ type: "text", value: span.value }],
    });
    cursor = span.end;
  }
  if (cursor < text.length) {
    out.push({ type: "text", value: text.slice(cursor) });
  }
  return out;
}
