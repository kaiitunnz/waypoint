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
