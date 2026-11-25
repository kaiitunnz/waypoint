import { parseEvent, readToolName, toolFilePath, type EventDiffPreview } from "../events";
import { isTodoListEvent, readTodoEntries, summarizeTodos, type TodoEntry } from "../todos";
import type { EventRecord } from "../types";
import { relativeToCwd, relativizeInText, splitPath } from "../workspacePaths";
import {
  collapseWhitespace,
  isAgentToolName,
  isFileEditToolName,
  previewForToolEvent,
  toolBadgeFor,
  toolInputForEvent,
} from "./toolPresentation";
import { itemKey, primaryEvent, type LeafItem } from "./transcript";

export interface ToolRunCounts {
  bash: number;
  edit: number;
  read: number;
  todo: number;
  agent: number;
  skill: number;
  monitor: number;
  other: number;
}

// The buckets a collapsed run's glyph chips count. `read` covers Read, Grep and Glob.
export function toolRunCounts(toolNames: readonly string[]): ToolRunCounts {
  const counts: ToolRunCounts = { bash: 0, edit: 0, read: 0, todo: 0, agent: 0, skill: 0, monitor: 0, other: 0 };
  for (const name of toolNames) {
    if (name === "Bash") counts.bash += 1;
    else if (isFileEditToolName(name)) counts.edit += 1;
    else if (name === "Read" || name === "Grep" || name === "Glob") counts.read += 1;
    else if (name === "TodoWrite") counts.todo += 1;
    else if (isAgentToolName(name)) counts.agent += 1;
    else if (name === "Skill") counts.skill += 1;
    else if (name === "Monitor") counts.monitor += 1;
    else counts.other += 1;
  }
  return counts;
}

// The tool each call of a run used. Codex's todo event carries no tool name, so
// every todo list counts as "TodoWrite".
export function toolNamesOfRun(items: readonly LeafItem[]): string[] {
  const names: string[] = [];
  for (const child of items) {
    const event = primaryEvent(child);
    if (isTodoListEvent(event)) {
      names.push("TodoWrite");
      continue;
    }
    const name = readToolName(event);
    if (name !== null) names.push(name);
  }
  return names;
}

type Category =
  | "read"
  | "search"
  | "edit"
  | "bash"
  | "web"
  | "agent"
  | "followup"
  | "skill"
  | "monitor"
  | "todo"
  | "other";

function categoryOf(tool: string | null): Category {
  switch (tool) {
    case "Read":
      return "read";
    case "Grep":
    case "Glob":
      return "search";
    case "Bash":
      return "bash";
    case "WebFetch":
    case "WebSearch":
      return "web";
    case "Skill":
      return "skill";
    case "Monitor":
      return "monitor";
    case "TodoWrite":
      return "todo";
    // Waiting on or closing a subagent is part of one subagent's life, not another one.
    case "Wait":
    case "closeAgent":
      return "followup";
    default:
      if (isFileEditToolName(tool)) return "edit";
      if (isAgentToolName(tool)) return "agent";
      return "other";
  }
}

const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

const PHRASES: Record<Category, (count: number) => string> = {
  read: (n) => `read ${plural(n, "file")}`,
  search: (n) => `searched ${plural(n, "pattern")}`,
  edit: (n) => `edited ${plural(n, "file")}`,
  bash: (n) => `ran ${plural(n, "command")}`,
  web: (n) => (n === 1 ? "searched the web" : `searched the web ${n} times`),
  agent: (n) => `ran ${plural(n, "subagent")}`,
  followup: (n) => `followed up on ${plural(n, "subagent")}`,
  skill: (n) => `used ${plural(n, "skill")}`,
  monitor: (n) => `watched ${plural(n, "task")}`,
  todo: () => "updated the task list",
  other: (n) => `used ${plural(n, "other tool")}`,
};

// Reading order of a run's phrases.
const PHRASE_ORDER: Category[] = [
  "read",
  "search",
  "edit",
  "bash",
  "web",
  "agent",
  "followup",
  "skill",
  "monitor",
  "todo",
  "other",
];

// The chips on a collapsed run: a glyph and a count, in a fixed order.
export interface RunGlyph {
  glyph: string;
  count: number;
}

export interface ToolRunSummary {
  // "Read 3 files, searched 2 patterns, ran 1 command".
  phrase: string;
  glyphs: RunGlyph[];
  additions: number;
  deletions: number;
}

function isToolItem(child: LeafItem): boolean {
  if (child.kind === "pair") return true;
  return child.event.kind === "tool_call" || child.event.kind === "tool_result";
}

function categoryOfChild(child: LeafItem): Category | null {
  const event = primaryEvent(child);
  // A todo list event with no tool name is still a todo call.
  if (isTodoListEvent(event)) return "todo";
  return isToolItem(child) ? categoryOf(readToolName(event)) : null;
}

function editedPath(child: LeafItem): string | null {
  const events = child.kind === "pair" ? [child.pair.call, child.pair.result] : [child.event];
  for (const event of events) {
    if (!event) continue;
    const path = toolFilePath(toolInputForEvent(event));
    if (path) return path;
  }
  return null;
}

function childDiffPreview(child: LeafItem): EventDiffPreview | null {
  if (child.kind === "pair") {
    return (
      (child.pair.result ? parseEvent(child.pair.result).diffPreview : null) ??
      (child.pair.call ? parseEvent(child.pair.call).diffPreview : null) ??
      null
    );
  }
  return parseEvent(child.event).diffPreview ?? null;
}

function sentence(parts: string[]): string {
  const joined = parts.join(", ");
  return joined ? joined.charAt(0).toUpperCase() + joined.slice(1) : "";
}

// A run in plain words, plus the counts its collapsed line carries.
export function summarizeToolRun(items: readonly LeafItem[]): ToolRunSummary {
  const counts = new Map<Category, number>();
  const editedFiles = new Set<string>();
  let unnamedEdits = 0;
  let additions = 0;
  let deletions = 0;
  for (const child of items) {
    const category = categoryOfChild(child);
    if (category === null) continue;
    if (category === "edit") {
      const path = editedPath(child);
      if (path) editedFiles.add(path);
      else unnamedEdits += 1;
      const diff = childDiffPreview(child);
      if (diff) {
        additions += diff.totalAdditions;
        deletions += diff.totalDeletions;
      }
      continue;
    }
    counts.set(category, (counts.get(category) ?? 0) + 1);
  }
  const edits = editedFiles.size + unnamedEdits;
  if (edits > 0) counts.set("edit", edits);
  // The follow-up calls of a subagent the run started are already counted with it.
  if (counts.has("agent")) counts.delete("followup");

  const parts = PHRASE_ORDER.flatMap((category) => {
    const count = counts.get(category);
    return count ? [PHRASES[category](count)] : [];
  });

  const read = (counts.get("read") ?? 0) + (counts.get("search") ?? 0);
  const glyphs: RunGlyph[] = [
    { glyph: "▤", count: read },
    { glyph: "✎", count: edits },
    { glyph: "›_", count: counts.get("bash") ?? 0 },
    { glyph: "☑", count: counts.get("todo") ?? 0 },
    { glyph: "◇", count: (counts.get("agent") ?? 0) + (counts.get("followup") ?? 0) },
    { glyph: "❖", count: counts.get("skill") ?? 0 },
    { glyph: "◉", count: counts.get("monitor") ?? 0 },
    { glyph: "ƒ", count: (counts.get("web") ?? 0) + (counts.get("other") ?? 0) },
  ].filter((entry) => entry.count > 0);

  return { phrase: sentence(parts), glyphs, additions, deletions };
}

export interface CallRow {
  key: string;
  glyph: string;
  // The tool's own name: "Bash", "Edit", "Read".
  tool: string | null;
  // What the call touched: a path, a command, a pattern; a todo list's progress.
  label: string;
  // A todo list's entries.
  todos: TodoEntry[] | null;
  // Set for file tools so the directory can be muted; relative to the working directory when inside it.
  path: { dir: string; base: string } | null;
  // The row as the agent gave it, paths unshortened, for a tooltip.
  title: string;
  mono: boolean;
  additions: number | null;
  deletions: number | null;
  status: "pending" | "done" | "failed";
  durationMs: number | null;
  // The last meaningful line of a command's output.
  output: string | null;
  diff: EventDiffPreview | null;
}

function lastLine(text: string): string | null {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.length > 0 ? lines[lines.length - 1].slice(0, 200) : null;
}

// Under half a second the two timestamps say when the events were recorded, not how long the call ran.
const MIN_DURATION_MS = 500;

function durationBetween(call: EventRecord | null, result: EventRecord | null): number | null {
  if (!call || !result) return null;
  const ms = Date.parse(result.ts) - Date.parse(call.ts);
  return Number.isFinite(ms) && ms >= MIN_DURATION_MS ? ms : null;
}

// "1.8s", "42s", "2m 4s".
export function formatCallDuration(ms: number): string {
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

// One compact row of an expanded run.
export function describeCall(child: LeafItem, cwd?: string | null): CallRow | null {
  const call = child.kind === "pair" ? child.pair.call : child.event.kind === "tool_call" ? child.event : null;
  const result = child.kind === "pair" ? child.pair.result : child.event.kind === "tool_result" ? child.event : null;
  const event = call ?? result;
  if (!event) return null;
  const todo = isTodoListEvent(event);
  const tool = todo ? "TodoWrite" : readToolName(event);
  const badge = toolBadgeFor(tool);
  const diff = childDiffPreview(child);
  const input = toolInputForEvent(event);
  // A search's `path` is where it looks, not what it is about: its row shows the pattern.
  const filePath =
    categoryOf(tool) === "search"
      ? null
      : (toolFilePath(input) ?? (isFileEditToolName(tool) ? (diff?.files[0]?.path ?? null) : null));
  const command = tool === "Bash" && typeof input?.command === "string" ? input.command : null;
  const shownPath = filePath === null ? null : relativeToCwd(filePath, cwd);
  const preview = previewForToolEvent(event, badge.label);
  const commandText = command ? collapseWhitespace(command) : null;
  const todos = tool === "TodoWrite" ? ([result, call].map((e) => (e ? readTodoEntries(e) : null)).find((entries) => entries !== null) ?? null) : null;
  const progress = todos ? summarizeTodos(todos) : null;
  const label = todos
    ? progress
      ? `Tasks ${progress.completed} of ${progress.total} done`
      : "Tasks"
    : (shownPath ?? relativizeInText(preview, cwd));
  const stats = diff && isFileEditToolName(tool) ? diff : null;
  return {
    key: itemKey(child),
    glyph: badge.glyph,
    tool,
    label: commandText ?? label,
    todos,
    path: shownPath ? splitPath(shownPath) : null,
    title: commandText ?? filePath ?? preview,
    mono: filePath !== null || command !== null,
    additions: stats ? stats.totalAdditions : null,
    deletions: stats ? stats.totalDeletions : null,
    status: !result ? "pending" : result.metadata?.is_error === true ? "failed" : "done",
    durationMs: durationBetween(call, result),
    output: tool === "Bash" && result ? lastLine(result.text) : null,
    diff,
  };
}
