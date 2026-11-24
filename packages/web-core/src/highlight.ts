// Lazy syntax highlighting for the workspace file preview. lowlight (the hast
// interface to highlight.js) is dynamically imported so its language grammars
// stay out of the initial bundle — the file preview is a modal opened on
// demand. We tokenize to a hast tree and split it into per-line token arrays so
// the preview keeps its custom line-number gutter and soft-wrap behavior.

export interface HighlightToken {
  text: string;
  className: string;
}

// Skip highlighting past this size: content is already backend-capped at
// ~200 KB, but minified blobs can still jank the tokenizer.
const MAX_HIGHLIGHT_CHARS = 120_000;

const EXTENSION_LANGUAGE: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  mts: "typescript",
  cts: "typescript",
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  py: "python",
  rb: "ruby",
  go: "go",
  rs: "rust",
  java: "java",
  c: "c",
  h: "c",
  cc: "cpp",
  cpp: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  cs: "csharp",
  php: "php",
  swift: "swift",
  kt: "kotlin",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  json: "json",
  jsonc: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  ini: "ini",
  xml: "xml",
  html: "xml",
  htm: "xml",
  svg: "xml",
  vue: "xml",
  css: "css",
  scss: "scss",
  sass: "scss",
  less: "less",
  md: "markdown",
  markdown: "markdown",
  sql: "sql",
  diff: "diff",
  patch: "diff",
  graphql: "graphql",
  gql: "graphql",
  lua: "lua",
  r: "r",
  pl: "perl",
};

function languageForPath(path: string): string | null {
  const name = path.split("/").pop() ?? path;
  const dot = name.lastIndexOf(".");
  if (dot < 0) return null;
  return EXTENSION_LANGUAGE[name.slice(dot + 1).toLowerCase()] ?? null;
}

// Resolve a markdown fence info string (the token after the opening ```) to an
// hljs language name. Many fence tokens are already hljs names (python, go,
// rust); the rest are the same aliases we map for file extensions (ts, py,
// yml, sh). The token itself is the last resort — `registered()` downstream
// rejects anything lowlight doesn't know, so a bad guess just falls back to
// raw rendering.
function languageForFence(info: string): string | null {
  const token = info.trim().toLowerCase().split(/\s+/)[0];
  if (!token) return null;
  return EXTENSION_LANGUAGE[token] ?? token;
}

interface HastNode {
  type: string;
  value?: string;
  tagName?: string;
  properties?: { className?: string[] };
  children?: HastNode[];
}

interface LowlightLike {
  registered(name: string): boolean;
  highlight(language: string, value: string): HastNode;
}

let lowlightPromise: Promise<LowlightLike> | null = null;

function getLowlight(): Promise<LowlightLike> {
  if (!lowlightPromise) {
    lowlightPromise = import("lowlight").then(({ createLowlight, common }) =>
      createLowlight(common),
    ) as Promise<LowlightLike>;
  }
  return lowlightPromise;
}

function flatten(nodes: HastNode[], inherited: string, out: HighlightToken[]): void {
  for (const node of nodes) {
    if (node.type === "text") {
      out.push({ text: node.value ?? "", className: inherited });
    } else if (node.type === "element" && node.children) {
      const own = (node.properties?.className ?? []).join(" ");
      const combined = own ? (inherited ? `${inherited} ${own}` : own) : inherited;
      flatten(node.children, combined, out);
    }
  }
}

function splitIntoLines(tokens: HighlightToken[]): HighlightToken[][] {
  const lines: HighlightToken[][] = [[]];
  for (const token of tokens) {
    const parts = token.text.split("\n");
    parts.forEach((part, index) => {
      if (index > 0) lines.push([]);
      if (part) lines[lines.length - 1].push({ text: part, className: token.className });
    });
  }
  return lines;
}

// Returns per-line token arrays, or null when highlighting is unavailable
// (oversized content, unregistered grammar, or a parse error) — callers fall
// back to rendering the raw lines.
async function highlightCodeToLines(
  code: string,
  language: string | null,
): Promise<HighlightToken[][] | null> {
  if (!language || code.length > MAX_HIGHLIGHT_CHARS) return null;
  const lowlight = await getLowlight();
  if (!lowlight.registered(language)) return null;
  try {
    const tree = lowlight.highlight(language, code);
    const tokens: HighlightToken[] = [];
    flatten(tree.children ?? [], "", tokens);
    return splitIntoLines(tokens);
  } catch {
    return null;
  }
}

// Highlight a workspace file, inferring the language from its path/extension.
export function highlightToLines(
  code: string,
  path: string,
): Promise<HighlightToken[][] | null> {
  return highlightCodeToLines(code, languageForPath(path));
}

// Highlight a markdown fenced code block, using its info string as the
// language hint. Returns null (raw fallback) for unlabeled fences.
export function highlightFenceToLines(
  code: string,
  info: string,
): Promise<HighlightToken[][] | null> {
  return highlightCodeToLines(code, languageForFence(info));
}

export type DiffLineKind = "add" | "del" | "hunk" | "meta" | "context";

export interface DiffLine {
  kind: DiffLineKind;
  // Source line numbers: both for context, new-only for additions, old-only for
  // deletions, null for meta/hunk rows. Drive the diff gutters.
  oldNo: number | null;
  newNo: number | null;
  // Highlighted content tokens, WITHOUT the leading +/-/space marker. Renderers
  // supply the marker (DIFF_MARKER) or line-number gutters themselves.
  tokens: HighlightToken[];
}

// The gutter sign per kind. Renderers that want an inline marker (the transcript
// diff) prepend this; the explorer diff uses line-number gutters instead.
export const DIFF_MARKER: Record<DiffLineKind, string> = {
  add: "+",
  del: "-",
  context: " ",
  hunk: "",
  meta: "",
};

function classifyDiffLine(line: string): DiffLineKind {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (
    line.startsWith("diff --git") ||
    line.startsWith("index ") ||
    line.startsWith("new file") ||
    line.startsWith("deleted file") ||
    line.startsWith("rename ") ||
    line.startsWith("similarity ") ||
    line.startsWith("Binary files") ||
    line.startsWith("\\ ")
  ) {
    return "meta";
  }
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "context";
}

interface DiffStructRow {
  kind: DiffLineKind;
  oldNo: number | null;
  newNo: number | null;
  content: string; // marker-stripped for add/del/context; raw for meta/hunk
}

// Parse a unified diff into rows, tracking old/new line numbers from each hunk
// header. Single source of truth for classification + numbering, shared by the
// plain fallback and the highlighter.
function parseDiffStructure(diff: string): DiffStructRow[] {
  const body = diff.endsWith("\n") ? diff.slice(0, -1) : diff;
  const rows: DiffStructRow[] = [];
  let oldLine = 0;
  let newLine = 0;
  let inHunk = false;
  for (const line of body.split("\n")) {
    const kind = classifyDiffLine(line);
    if (kind === "hunk") {
      const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
      if (match) {
        oldLine = Number(match[1]);
        newLine = Number(match[2]);
        inHunk = true;
      }
      rows.push({ kind, oldNo: null, newNo: null, content: line });
      continue;
    }
    if (kind === "meta" || !inHunk) {
      rows.push({ kind: "meta", oldNo: null, newNo: null, content: line });
      continue;
    }
    const content = line.slice(1);
    if (kind === "add") {
      rows.push({ kind, oldNo: null, newNo: newLine++, content });
    } else if (kind === "del") {
      rows.push({ kind, oldNo: oldLine++, newNo: null, content });
    } else {
      rows.push({ kind: "context", oldNo: oldLine++, newNo: newLine++, content });
    }
  }
  return rows;
}

// Synchronous fallback: classified rows with content as a single plain token.
// Used as the initial render before async highlighting resolves, and as the
// permanent rendering when highlighting is unavailable.
export function buildPlainDiffLines(diff: string): DiffLine[] {
  return parseDiffStructure(diff).map((row) => ({
    kind: row.kind,
    oldNo: row.oldNo,
    newNo: row.newNo,
    tokens: [{ text: row.content, className: "" }],
  }));
}

// Highlight the code inside a unified diff. Reconstructs the "before" (context +
// deletions) and "after" (context + additions) sides as whole documents so the
// tokenizer sees multi-line context, highlights each, then re-threads the
// per-line tokens back onto their diff rows. Returns null (raw fallback) when
// the language is unknown or either side is too large to highlight.
export async function highlightDiffToLines(
  diff: string,
  path: string,
): Promise<DiffLine[] | null> {
  const language = languageForPath(path);
  if (!language) return null;
  const rows = parseDiffStructure(diff);

  const afterSrc: string[] = [];
  const beforeSrc: string[] = [];
  for (const row of rows) {
    if (row.kind === "add" || row.kind === "context") afterSrc.push(row.content);
    if (row.kind === "del" || row.kind === "context") beforeSrc.push(row.content);
  }

  const afterHl = afterSrc.length
    ? await highlightCodeToLines(afterSrc.join("\n"), language)
    : [];
  const beforeHl = beforeSrc.length
    ? await highlightCodeToLines(beforeSrc.join("\n"), language)
    : [];
  if (afterHl === null || beforeHl === null) return null;

  let ai = 0;
  let bi = 0;
  return rows.map((row) => {
    if (row.kind === "meta" || row.kind === "hunk") {
      return {
        kind: row.kind,
        oldNo: null,
        newNo: null,
        tokens: [{ text: row.content, className: "" }],
      };
    }
    let tokens: HighlightToken[];
    if (row.kind === "del") {
      tokens = beforeHl[bi++] ?? [{ text: row.content, className: "" }];
    } else {
      // add or context both consume the "after" side; context also advances
      // the "before" cursor to keep the two queues aligned.
      tokens = afterHl[ai++] ?? [{ text: row.content, className: "" }];
      if (row.kind === "context") bi++;
    }
    return { kind: row.kind, oldNo: row.oldNo, newNo: row.newNo, tokens };
  });
}
