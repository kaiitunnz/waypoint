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

export function formatCwdSegments(cwd: string): string[] {
  if (!cwd) return [""];
  // Render the path as breadcrumb-style segments; an absolute path keeps its
  // leading slash on the first segment.
  const trimmed = cwd.replace(/\/+$/, "");
  const segments = trimmed.split("/").filter(Boolean);
  if (trimmed.startsWith("/")) {
    return [`/${segments[0]}`, ...segments.slice(1)];
  }
  return segments.length ? segments : [trimmed];
}

const trimCwd = (cwd: string | null | undefined) => cwd?.replace(/\/+$/, "");

export function splitPath(path: string): { dir: string; base: string } {
  const slash = path.lastIndexOf("/");
  return slash === -1 ? { dir: "", base: path } : { dir: path.slice(0, slash + 1), base: path.slice(slash + 1) };
}

const NAME_TAIL = 6;
const MAX_EXTENSION = 10;

// A file name as a head that may be cut short and a tail that stays: the extension
// and the characters before it.
export function splitFileName(name: string): { head: string; tail: string } {
  // By code point, so no character is cut in half.
  const chars = Array.from(name);
  const dot = chars.lastIndexOf(".");
  const extension = dot > 0 && chars.length - dot <= MAX_EXTENSION ? chars.length - dot : 0;
  const at = Math.max(0, chars.length - extension - NAME_TAIL);
  return { head: chars.slice(0, at).join(""), tail: chars.slice(at).join("") };
}

// CJK and emoji take two columns of a monospace font.
const WIDE = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}\p{Emoji_Presentation}\u3000-\u303f\uff00-\uff60\uffe0-\uffe6]/u;

export function textColumns(text: string): number {
  return Array.from(text).reduce((columns, char) => columns + (WIDE.test(char) ? 2 : 1), 0);
}

// A path as the session's own files read: relative to `cwd` when it lies inside it,
// otherwise unchanged.
export function relativeToCwd(path: string, cwd: string | null | undefined): string {
  const root = trimCwd(cwd);
  if (!root) return path;
  if (path === root) return ".";
  return path.startsWith(`${root}/`) ? path.slice(root.length).replace(/^\/+/, "") || "." : path;
}

// A path as given to a session working in `cwd`, made absolute; null when it is
// relative and `cwd` is unknown.
export function resolveFromCwd(path: string, cwd: string | null | undefined): string | null {
  const absolute = path.startsWith("/");
  if (!absolute && !cwd) return null;
  const parts: string[] = [];
  for (const part of (absolute ? path : `${cwd}/${path}`).split("/")) {
    if (part === "..") parts.pop();
    else if (part !== "" && part !== ".") parts.push(part);
  }
  return `/${parts.join("/")}`;
}

// Every path inside `cwd` that appears in free text, made relative to it.
export function relativizeInText(text: string, cwd: string | null | undefined): string {
  const root = trimCwd(cwd);
  if (!root) return text;
  const escaped = root.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const inside = new RegExp(`(^|[^\\w./~-])${escaped}(?:/(\\S*)|(?![\\w./-]))`, "g");
  return text.replace(inside, (_match, lead: string, rest: string | undefined) => `${lead}${rest || "."}`);
}
