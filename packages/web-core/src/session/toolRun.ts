import { describeStep, type Step } from "../activity";
import { attachmentSpecsFor } from "../attachments";
import { parseEvent, parseTaskNotification, readSummary, readToolName, toolFilePath, type EventDiffPreview } from "../events";
import { isTodoListEvent, readTodoEntries, summarizeTodos, type TodoEntry } from "../todos";
import type { AttachmentSpec, EventRecord } from "../types";
import { relativeToCwd, relativizeInText, splitPath } from "../workspacePaths";
import { formatDuration } from "./cardText";
import { foldLines } from "./foldText";
import { taskNoteReport } from "./taskNoteBody";
import { categoryOf, familyOf, toolFamilyOf, type ToolCategory, type ToolFamily } from "./toolFamily";
import {
  callArgumentsForEvent,
  collapseWhitespace,
  commandForEvent,
  commandOutputForEvent,
  delegationForEvent,
  filledText,
  isAgentToolName,
  isFileEditToolName,
  previewForToolEvent,
  resultContentForEvent,
  subagentDescription,
  subagentRequestForEvent,
  toolBadgeFor,
  toolInputForEvent,
  toolNameParts,
  toolPairAttachments,
  type ToolNameParts,
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

const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

const PHRASES: Record<ToolCategory, (count: number) => string> = {
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

const PHRASE_ORDER: ToolCategory[] = [
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

// The chips on a collapsed run: a glyph, what it counts and the count, in a fixed order.
export interface RunGlyph {
  glyph: string;
  label: string;
  count: number;
  family: ToolFamily | null;
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

function categoryOfChild(child: LeafItem): ToolCategory | null {
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
  const counts = new Map<ToolCategory, number>();
  const editedFiles = new Set<string>();
  let unnamedEdits = 0;
  let additions = 0;
  let deletions = 0;
  for (const child of items) {
    const category = categoryOfChild(child);
    if (category === null) continue;
    if (category === "edit") {
      const diff = childDiffPreview(child);
      const path = editedPath(child);
      const paths = path ? [path] : (diff?.files.map((file) => file.path).filter(Boolean) ?? []);
      if (paths.length > 0) for (const name of paths) editedFiles.add(name);
      else unnamedEdits += 1;
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
    { glyph: "▤", label: "Reads and searches", count: read, family: familyOf("read") },
    { glyph: "✎", label: "Edited files", count: edits, family: familyOf("edit") },
    { glyph: "›_", label: "Commands", count: counts.get("bash") ?? 0, family: familyOf("bash") },
    { glyph: "☑", label: "Task updates", count: counts.get("todo") ?? 0, family: familyOf("todo") },
    { glyph: "◇", label: "Subagents", count: (counts.get("agent") ?? 0) + (counts.get("followup") ?? 0), family: familyOf("agent") },
    { glyph: "❖", label: "Skills", count: counts.get("skill") ?? 0, family: familyOf("skill") },
    { glyph: "◉", label: "Watched tasks", count: counts.get("monitor") ?? 0, family: familyOf("monitor") },
    // Web calls count here with every other tool, so the chip has no one family.
    { glyph: "ƒ", label: "Other tools", count: (counts.get("web") ?? 0) + (counts.get("other") ?? 0), family: null },
  ].filter((entry) => entry.count > 0);

  return { phrase: sentence(parts), glyphs, additions, deletions };
}

export interface CallReply {
  // A subagent's report is prose; any other tool's output is shown as it is.
  kind: "report" | "output";
  text: string;
  // `text` is the start of a longer report.
  cut: boolean;
}

export interface CallView {
  key: string;
  glyph: string;
  family: ToolFamily | null;
  // The agent's description of the call, shown as its heading.
  summary: string | null;
  // The tool's name, for the glyph's accessible label.
  toolLabel: string;
  // Leads the row of a call whose label does not say which tool ran.
  name: ToolNameParts | null;
  // What the call touched: a path, a command, a pattern, its arguments; a todo list's
  // progress. Empty where the name says it all.
  label: string;
  todos: TodoEntry[] | null;
  // Set for file tools so the directory can be muted; relative to the working directory when inside it.
  path: { dir: string; base: string } | null;
  // Tooltip: a named call's raw tool name, else the row with its paths unshortened.
  title: string;
  mono: boolean;
  // The label is a shell command.
  shell: boolean;
  additions: number | null;
  deletions: number | null;
  status: "pending" | "done" | "failed";
  durationMs: number | null;
  // Set on a call that drives a subagent: the kind it started, and in Markdown what it asked.
  agentType: string | null;
  prompt: string | null;
  reply: CallReply | null;
  diff: EventDiffPreview | null;
  attachments: AttachmentSpec[];
}

// The output without the blank lines around it and with `\n` line ends.
function cleanOutput(text: string): string | null {
  return text.replace(/\r\n?/g, "\n").replace(/^(?:[ \t]*\n)+/, "").trimEnd() || null;
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
  return ms < 9_950 ? `${(ms / 1000).toFixed(1)}s` : formatDuration(ms);
}

// A call's arguments as the lines a person would read: a query and where it looks, a URL,
// or one `name: value` line per argument. Null for a call with nothing to show that way.
function argumentLines(
  tool: string | null,
  input: Record<string, unknown> | null,
  cwd: string | null | undefined,
  summary: string | null,
): string | null {
  if (!input) return null;
  const text = (value: unknown) => (typeof value === "string" ? value : JSON.stringify(value) ?? "");
  switch (categoryOf(tool)) {
    case "search": {
      const where = typeof input.path === "string" ? relativeToCwd(input.path, cwd) : "";
      return [text(input.pattern), where].filter(Boolean).join("\n") || null;
    }
    case "web":
      return text(input.url ?? input.query) || null;
    case "skill":
    case "monitor":
    case "followup":
    case "other": {
      // An argument the heading already says is left out; a command never is. A summary
      // is one line, so the argument is compared as one.
      const said = (value: unknown) =>
        summary !== null && typeof value === "string" && collapseWhitespace(value) === collapseWhitespace(summary);
      return (
        Object.entries(input)
          .filter(([name, value]) => name === "command" || !said(value))
          .map(([name, value]) => `${name}: ${text(value)}`)
          .join("\n") || null
      );
    }
    default:
      return null;
  }
}

const NAMED_CATEGORIES: ReadonlySet<ToolCategory> = new Set(["other", "monitor", "followup"]);

const replyWith = (kind: CallReply["kind"], text: string | null, cut = false): CallReply | null =>
  text ? { kind, text, cut } : null;

function replyOf(call: EventRecord | null, result: EventRecord, tool: string | null, prompted: boolean): CallReply | null {
  const text = cleanOutput(resultContentForEvent(result) ?? commandOutputForEvent(result));
  if (tool === "Bash" || result.metadata?.is_error === true) return replyWith("output", text);
  const delegation = delegationForEvent(result);
  if (delegation) return replyWith("report", delegation.report);
  const category = categoryOf(tool);
  // Only a call that carried a prompt answers with a report.
  if (category === "agent") return prompted ? replyWith("report", text) : null;
  // categoryOf(null) is "other": a result whose call is not loaded may name no tool.
  if (tool === null || category !== "other") return null;
  return text === call?.text.trim() || text === tool ? null : replyWith("output", text);
}

interface Outcome {
  status: CallView["status"];
  endedBy: EventRecord | null;
  reply: CallReply | null;
  // The summary of a task notification that stands in for the result, and the files of a
  // report it cut.
  summary: string | null;
  attachments: AttachmentSpec[];
}

const NO_NOTICE = { summary: null, attachments: [] };

// A task notification stands in for a call's result when it reports an agent, when the
// result is deferred (it only acknowledges the launch), and for any subagent call.
function outcomeOf(
  call: EventRecord | null,
  result: EventRecord | null,
  tool: string | null,
  report: EventRecord | undefined,
  prompted: boolean,
): Outcome {
  const deferred = result?.metadata?.result_deferred === true;
  const note = report ? parseTaskNotification(report) : null;
  if (report && note && (note.kind === "agent" || deferred || categoryOf(tool) === "agent")) {
    const preview = note.resultPreview ? cleanOutput(note.resultPreview) : null;
    return {
      status: note.status && note.status !== "completed" ? "failed" : "done",
      endedBy: report,
      reply: replyWith(note.kind === "agent" ? "report" : "output", preview, note.resultTruncated),
      summary: note.summary,
      // An uncut or absent report leaves only the agent's raw output file, if any.
      attachments: note.resultTruncated ? taskNoteReport(note, report).specs : [],
    };
  }
  if (!result || deferred) return { ...NO_NOTICE, status: "pending", endedBy: null, reply: null };
  return {
    ...NO_NOTICE,
    status: result.metadata?.is_error === true ? "failed" : "done",
    endedBy: result,
    reply: replyOf(call, result, tool, prompted),
  };
}

export function pendingCallOf(items: readonly LeafItem[]): LeafItem | undefined {
  return items.findLast((child) => (child.kind === "pair" ? !child.pair.result : child.event.kind === "tool_call"));
}

function oneLine(command: string | null): string | null {
  const { text, hidden } = foldLines(command?.trim() ?? "", 1);
  if (!text) return null;
  return hidden > 0 ? `${text.trimEnd()} …` : text;
}

// Only a Bash call counts: Monitor and PowerShell calls read as `name: value` arguments.
const isShellTool = (tool: string | null): boolean => categoryOf(tool) === "bash";

export interface LiveCall {
  step: Step | null;
  // The agent's description of the call.
  summary: string | null;
  // What the call runs, on one line.
  command: string | null;
  // The command is a shell command.
  shell: boolean;
}

// What the live run line says of the call in progress.
export function liveCall(items: readonly LeafItem[], working: boolean): LiveCall | null {
  const pending = working ? pendingCallOf(items) : undefined;
  if (!pending) return null;
  const event = primaryEvent(pending);
  const command = oneLine(commandForEvent(event));
  return { step: describeStep(event), summary: readSummary(event), command, shell: command !== null && isShellTool(readToolName(event)) };
}

export function describeCall(child: LeafItem, cwd?: string | null): CallView | null {
  const call = child.kind === "pair" ? child.pair.call : child.event.kind === "tool_call" ? child.event : null;
  const result = child.kind === "pair" ? child.pair.result : child.event.kind === "tool_result" ? child.event : null;
  const event = call ?? result;
  if (!event) return null;
  const todo = isTodoListEvent(event);
  const tool = todo ? "TodoWrite" : readToolName(event);
  const badge = toolBadgeFor(tool);
  const diff = childDiffPreview(child);
  const input = toolInputForEvent(event);
  const args = callArgumentsForEvent(event);
  const category = categoryOf(tool);
  const summary = call ? readSummary(call) : null;
  const command = isShellTool(tool) ? commandForEvent(event) : null;
  const subject = (command?.replace(/\r\n?/g, "\n").trim() || null) ?? argumentLines(tool, args, cwd, summary);
  // A call whose arguments read as lines keeps them all; a path alone stands for a file tool.
  const filePath =
    subject !== null ? null : (toolFilePath(input) ?? (isFileEditToolName(tool) ? (diff?.files[0]?.path ?? null) : null));
  const shownPath = filePath === null ? null : relativeToCwd(filePath, cwd);
  const delegation = delegationForEvent(event);
  // Another tool's `prompt` or `agent` argument is not a request to a subagent.
  const request = category === "agent" || delegation ? subagentRequestForEvent(event) : null;
  const outcome = outcomeOf(call, result, tool, child.kind === "pair" ? child.pair.report : undefined, Boolean(request?.prompt));
  // A launch acknowledgement says nothing about a call that is not loaded.
  const preview =
    !call && result?.metadata?.result_deferred === true
      ? (outcome.summary ?? "Background task")
      : previewForToolEvent(event, badge.label);
  const todos = tool === "TodoWrite" ? ([result, call].map((e) => (e ? readTodoEntries(e) : null)).find((entries) => entries !== null) ?? null) : null;
  const progress = todos ? summarizeTodos(todos) : null;
  const label = todos
    ? progress
      ? `Tasks ${progress.completed} of ${progress.total} done`
      : "Tasks"
    : (shownPath ?? relativizeInText(preview, cwd));
  const stats = diff && isFileEditToolName(tool) ? diff : null;

  const description = category === "agent" ? filledText(subagentDescription(event)) : null;

  // A result alone, such as a spawned subagent's notice, is labelled by its text.
  const named = tool !== null && (NAMED_CATEGORIES.has(category) || (category === "agent" && call !== null && !description));
  // A named row's preview says only its name again, or the output a result alone shows below.
  const repeated =
    args !== null ||
    delegation !== null ||
    preview === "No output" ||
    preview === tool ||
    (!call && outcome.reply?.kind === "output");

  // A subagent call's label is its description, which the heading already says.
  const echoesSummary = summary !== null && subject === null && preview === summary;

  return {
    key: itemKey(child),
    glyph: badge.glyph,
    family: toolFamilyOf(tool),
    summary,
    toolLabel: badge.label,
    name: named ? toolNameParts(tool) : null,
    label: echoesSummary ? "" : (subject ?? (named && repeated ? "" : label)),
    todos,
    path: shownPath ? splitPath(shownPath) : null,
    title: named ? tool : (filePath ?? preview),
    mono: filePath !== null || subject !== null,
    shell: command !== null && subject !== null,
    additions: stats ? stats.totalAdditions : null,
    deletions: stats ? stats.totalDeletions : null,
    status: outcome.status,
    durationMs: durationBetween(call, outcome.endedBy),
    agentType: request?.agentType ?? null,
    prompt: request?.prompt ?? null,
    reply: outcome.reply,
    diff,
    attachments: [
      ...(child.kind === "pair" ? toolPairAttachments(child.pair) : attachmentSpecsFor(child.event)),
      ...outcome.attachments,
    ],
  };
}
