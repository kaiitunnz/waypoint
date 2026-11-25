import { normalizeToolName, parseEvent, readToolName, toolFilePath, type EventDiffPreview } from "../events";
import { isTodoListEvent } from "../todos";
import type { EventRecord } from "../types";
import {
  isAgentToolName,
  isFileEditToolName,
  previewForToolEvent,
  toolBadgeFor,
  toolInputForEvent,
} from "./toolPresentation";
import type { TranscriptItem } from "./transcript";

type RunChild = Extract<TranscriptItem, { kind: "single" | "pair" }>;

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
export function toolNamesOfRun(items: readonly RunChild[]): string[] {
  const names: string[] = [];
  for (const child of items) {
    const event = child.kind === "pair" ? (child.pair.call ?? child.pair.result) : child.event;
    if (!event) continue;
    if (isTodoListEvent(event)) {
      names.push("TodoWrite");
      continue;
    }
    const name = readToolName(event);
    if (name !== null) names.push(name);
  }
  return names;
}

type Category = "read" | "search" | "edit" | "bash" | "web" | "agent" | "skill" | "monitor" | "todo" | "other";

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
  skill: (n) => `used ${plural(n, "skill")}`,
  monitor: (n) => `watched ${plural(n, "task")}`,
  todo: () => "updated the task list",
  other: (n) => `used ${plural(n, "other tool")}`,
};

// Reading order of a run's phrases.
const PHRASE_ORDER: Category[] = ["read", "search", "edit", "bash", "web", "agent", "skill", "monitor", "todo", "other"];

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
  calls: number;
}

function callEvents(child: RunChild): EventRecord | null {
  return child.kind === "pair" ? (child.pair.call ?? child.pair.result) : child.event;
}

function isToolItem(child: RunChild): boolean {
  if (child.kind === "pair") return true;
  return child.event.kind === "tool_call" || child.event.kind === "tool_result";
}

function categoryOfChild(child: RunChild): Category | null {
  const event = callEvents(child);
  if (!event || !isToolItem(child)) {
    // A todo list event with no tool name is still a todo call.
    return event && isTodoListEvent(event) ? "todo" : null;
  }
  if (isTodoListEvent(event)) return "todo";
  return categoryOf(normalizeToolName(readToolName(event)));
}

function editedPath(child: RunChild): string | null {
  const events = child.kind === "pair" ? [child.pair.call, child.pair.result] : [child.event];
  for (const event of events) {
    if (!event) continue;
    const path = toolFilePath(toolInputForEvent(event));
    if (path) return path;
  }
  return null;
}

function childDiffPreview(child: RunChild): EventDiffPreview | null {
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
export function summarizeToolRun(items: readonly RunChild[]): ToolRunSummary {
  const counts = new Map<Category, number>();
  const editedFiles = new Set<string>();
  let unnamedEdits = 0;
  let additions = 0;
  let deletions = 0;
  let calls = 0;
  for (const child of items) {
    const category = categoryOfChild(child);
    if (category === null) continue;
    calls += 1;
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
    { glyph: "◇", count: counts.get("agent") ?? 0 },
    { glyph: "❖", count: counts.get("skill") ?? 0 },
    { glyph: "◉", count: counts.get("monitor") ?? 0 },
    { glyph: "ƒ", count: (counts.get("web") ?? 0) + (counts.get("other") ?? 0) },
  ].filter((entry) => entry.count > 0);

  return { phrase: sentence(parts), glyphs, additions, deletions, calls };
}

export interface CallRow {
  key: string;
  glyph: string;
  // The tool's own name: "Bash", "Edit", "Read".
  tool: string | null;
  // What the call touched: a path, a command, a pattern.
  label: string;
  // Set for file tools so the directory can be muted.
  path: { dir: string; base: string } | null;
  mono: boolean;
  additions: number | null;
  deletions: number | null;
  status: "pending" | "done" | "failed";
  durationMs: number | null;
  // The last meaningful line of a command's output.
  output: string | null;
  diff: EventDiffPreview | null;
}

export function splitPath(path: string): { dir: string; base: string } {
  const slash = path.lastIndexOf("/");
  return slash === -1 ? { dir: "", base: path } : { dir: path.slice(0, slash + 1), base: path.slice(slash + 1) };
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
export function describeCall(child: RunChild): CallRow | null {
  const call = child.kind === "pair" ? child.pair.call : child.event.kind === "tool_call" ? child.event : null;
  const result = child.kind === "pair" ? child.pair.result : child.event.kind === "tool_result" ? child.event : null;
  const event = call ?? result;
  if (!event || (child.kind === "single" && !isToolItem(child))) return null;
  const todo = isTodoListEvent(event);
  const tool = todo ? "TodoWrite" : normalizeToolName(readToolName(event));
  const badge = toolBadgeFor(tool);
  const diff = childDiffPreview(child);
  const filePath = toolFilePath(toolInputForEvent(event)) ?? (isFileEditToolName(tool) ? diff?.files[0]?.path ?? null : null);
  const input = toolInputForEvent(event);
  const command = tool === "Bash" && typeof input?.command === "string" ? input.command : null;
  const label = filePath ?? (call ? previewForToolEvent(call, badge.label) : result ? previewForToolEvent(result, badge.label) : "");
  const stats = diff && isFileEditToolName(tool) ? diff : null;
  return {
    key: child.kind === "pair" ? child.pair.itemId : `${child.event.sequence}`,
    glyph: badge.glyph,
    tool,
    label: command ? command.replace(/\s+/g, " ").trim() : label,
    path: filePath ? splitPath(filePath) : null,
    mono: filePath !== null || command !== null,
    additions: stats ? stats.totalAdditions : null,
    deletions: stats ? stats.totalDeletions : null,
    status: !result ? "pending" : result.metadata?.is_error === true ? "failed" : "done",
    durationMs: durationBetween(call, result),
    output: tool === "Bash" && result ? lastLine(result.text) : null,
    diff,
  };
}
