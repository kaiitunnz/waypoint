import {
  asRecord,
  FILE_EDIT_TOOLS,
  readToolName,
  toolFilePath,
  type EventDiffPreview,
} from "../events";
import type { AttachmentSpec, EventRecord } from "../types";
import { attachmentSpecsFor } from "../attachments";
import type { ToolPair } from "./transcript";

export interface ToolBadge {
  glyph: string;
  // A colour-theme key, so no icon assets ship.
  variant: string;
  label: string;
}

const TOOL_BADGES: Record<string, ToolBadge> = {
  Bash: { glyph: "›_", variant: "bash", label: "Bash" },
  Read: { glyph: "▤", variant: "read", label: "Read" },
  Edit: { glyph: "✎", variant: "edit", label: "Edit" },
  MultiEdit: { glyph: "✎", variant: "edit", label: "MultiEdit" },
  NotebookEdit: { glyph: "✎", variant: "edit", label: "NotebookEdit" },
  Write: { glyph: "✚", variant: "write", label: "Write" },
  Grep: { glyph: "⌕", variant: "grep", label: "Grep" },
  Glob: { glyph: "✱", variant: "glob", label: "Glob" },
  WebFetch: { glyph: "⌖", variant: "web", label: "WebFetch" },
  WebSearch: { glyph: "⌖", variant: "web", label: "WebSearch" },
  // Codex collaboration mode drives subagents through spawnAgent → wait (the
  // join, whose result carries the subagent's report) → closeAgent.
  Task: { glyph: "◇", variant: "task", label: "Task" },
  Agent: { glyph: "◇", variant: "task", label: "Agent" },
  spawnAgent: { glyph: "◇", variant: "task", label: "spawnAgent" },
  Wait: { glyph: "◈", variant: "task", label: "Wait" },
  closeAgent: { glyph: "◇", variant: "task", label: "closeAgent" },
  Subagent: { glyph: "◇", variant: "task", label: "Subagent" },
  Monitor: { glyph: "◉", variant: "monitor", label: "Monitor" },
  SendMessage: { glyph: "⇄", variant: "web", label: "SendMessage" },
  PushNotification: { glyph: "✉", variant: "web", label: "PushNotification" },
  ReadNotifications: { glyph: "✉", variant: "read", label: "ReadNotifications" },
  Skill: { glyph: "❖", variant: "skill", label: "Skill" },
  TodoWrite: { glyph: "☑", variant: "todo", label: "Todo" },
  AskUserQuestion: { glyph: "?", variant: "task", label: "Ask" },
};

// MCP and app-plugin tools arrive namespaced (``mcp__server__tool``,
// ``server:tool``); there is an open-ended set of them, so they are badged
// generically with the readable ``server·tool`` tail as the label.
function isPluginToolName(toolName: string): boolean {
  return toolName.includes("__") || toolName.includes(":");
}

function prettyPluginName(toolName: string): string {
  const stripped = toolName.startsWith("mcp__") ? toolName.slice(5) : toolName;
  return stripped.replace(/__/g, "·").replace(/:/g, "·");
}

export interface ToolNameParts {
  server: string | null;
  tool: string;
}

export function toolNameParts(name: string): ToolNameParts {
  const parts = /^mcp__(.+?)__(.+)$/.exec(name) ?? /^([^:]+):(.+)$/.exec(name);
  return parts ? { server: parts[1], tool: parts[2] } : { server: null, tool: name };
}

export function toolBadgeFor(toolName: string | null | undefined): ToolBadge {
  if (!toolName) {
    return { glyph: "→", variant: "default", label: "tool" };
  }
  const known = TOOL_BADGES[toolName];
  if (known) {
    return known;
  }
  if (isPluginToolName(toolName)) {
    return { glyph: "⧉", variant: "mcp", label: prettyPluginName(toolName) };
  }
  // Unlisted tools stay neutral by design: badging harness plumbing (waits,
  // polling, mode toggles) would add glare, not signal.
  return { glyph: "ƒ", variant: "default", label: toolName };
}

export function isFileEditToolName(toolName: string | null | undefined): boolean {
  return toolName != null && FILE_EDIT_TOOLS.has(toolName);
}

export function isAgentToolName(toolName: string | null | undefined): boolean {
  return (
    toolName === "Task" ||
    toolName === "Agent" ||
    toolName === "spawnAgent" ||
    toolName === "Wait" ||
    toolName === "closeAgent" ||
    toolName === "Subagent"
  );
}

// Claude carries a tool's input under the payload's `input`, other transports
// under `tool_input`.
export function toolInputForEvent(event: EventRecord): Record<string, unknown> | null {
  const meta = event.metadata as Record<string, unknown> | undefined;
  const payload = asRecord(meta?.payload);
  return asRecord(payload?.input) ?? asRecord(meta?.tool_input);
}

// The item a Codex tool call is about, carried under the started event's payload.
function startedItem(event: EventRecord): Record<string, unknown> | null {
  const meta = event.metadata as Record<string, unknown> | undefined;
  return asRecord(asRecord(meta?.item)?.item) ?? asRecord(asRecord(meta?.payload)?.item);
}

// Codex carries an MCP or plugin tool's arguments on its item.
export function callArgumentsForEvent(event: EventRecord): Record<string, unknown> | null {
  return toolInputForEvent(event) ?? asRecord(startedItem(event)?.arguments);
}

export const filledText = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value.trim() : null;

export interface Delegation {
  prompt: string | null;
  report: string | null;
}

export function delegationForEvent(event: EventRecord): Delegation | null {
  if (event.metadata?.item_type !== "collabAgentToolCall") return null;
  const item = startedItem(event);
  const messages = Object.values(asRecord(item?.agentsStates) ?? {}).flatMap(
    (state) => filledText(asRecord(state)?.message) ?? [],
  );
  // Closing a subagent repeats the report the wait gave.
  const report = readToolName(event) === "closeAgent" ? null : messages.join("\n\n") || null;
  return { prompt: filledText(item?.prompt), report };
}

// What a call asks of a subagent: its prompt, and the kind of subagent where the agent
// names one.
export function subagentRequestForEvent(event: EventRecord): { prompt: string | null; agentType: string | null } {
  const delegation = delegationForEvent(event);
  const input = isAgentToolName(readToolName(event)) ? toolInputForEvent(event) : null;
  return {
    prompt: delegation ? delegation.prompt : filledText(input?.prompt),
    agentType: filledText(input?.subagent_type) ?? filledText(input?.agent),
  };
}

const carriesBytes = (block: Record<string, unknown>) => "source" in block || "data" in block || "imageUrl" in block;

// Null leaves the event's own text to show. Media reads as its kind, which keeps its bytes
// out of the transcript.
export function resultContentForEvent(event: EventRecord): string | null {
  const items = startedItem(event)?.contentItems;
  const content = Array.isArray(items) ? items : asRecord(event.metadata?.payload)?.content;
  if (!Array.isArray(content)) return null;
  const blocks = content.flatMap((value) => {
    const block = asRecord(value);
    return block ? [block] : [];
  });
  if (!Array.isArray(items) && !blocks.some(carriesBytes)) return null;
  return (
    blocks
      .map((block) => (carriesBytes(block) ? `[${block.type}]` : (filledText(block.text) ?? JSON.stringify(block))))
      .join("\n") || null
  );
}

// `/bin/bash -lc "npm run lint"` reads as `npm run lint`.
function unwrapShell(command: string): string {
  const wrapped = /^(?:\/\S+\/)?(?:ba|z)?sh\s+-l?c\s+(["'])([\s\S]*)\1$/.exec(command.trim());
  return wrapped ? wrapped[2] : command;
}

// The shell command a call runs: Claude carries it in the tool input, Codex in the item.
export function commandForEvent(event: EventRecord): string | null {
  const command = toolInputForEvent(event)?.command ?? startedItem(event)?.command;
  return typeof command === "string" ? unwrapShell(command) : null;
}

// A command's own output. Codex's result text opens with the command line, so its item's output is read.
export function commandOutputForEvent(event: EventRecord): string {
  const output = startedItem(event)?.aggregatedOutput;
  return typeof output === "string" ? output : event.text;
}

// A file-edit tool whose backend sent no diff still names the file it touched.
export function fallbackDiffPreviewForFileEdit(
  events: (EventRecord | null | undefined)[],
  phase: EventDiffPreview["phase"],
): EventDiffPreview | null {
  for (const event of events) {
    if (!event || !isFileEditToolName(readToolName(event))) {
      continue;
    }
    const path = toolFilePath(toolInputForEvent(event));
    if (!path) {
      continue;
    }
    return {
      schemaVersion: 1,
      phase,
      files: [
        {
          path,
          oldPath: null,
          changeType: "update",
          diff: "",
          additions: 0,
          deletions: 0,
          truncated: false,
          binary: false,
          unavailableReason: "Diff preview was not included by the backend.",
        },
      ],
      totalAdditions: 0,
      totalDeletions: 0,
      truncated: false,
    };
  }
  return null;
}

// A pair's attachments may ride on its call or its result.
export function toolPairAttachments({ call, result }: ToolPair): AttachmentSpec[] {
  const seen = new Set<string>();
  const specs: AttachmentSpec[] = [];
  for (const event of [call, result]) {
    if (!event) continue;
    for (const spec of attachmentSpecsFor(event)) {
      if (seen.has(spec.id)) continue;
      seen.add(spec.id);
      specs.push(spec);
    }
  }
  return specs;
}

export function collapseWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function truncate(value: string, max: number): string {
  if (value.length <= max) {
    return value;
  }
  return `${value.slice(0, max - 1)}…`;
}

// A tool call or result in one line: the command, the path, the pattern.
export function previewForToolEvent(event: EventRecord, toolLabel: string): string {
  // Prefer a meaningful field from the structured tool input: event.text for
  // Claude tool_call events is `"ToolName\n{json input}"`, which is verbose and
  // repeats the tool name the badge already shows.
  const toolName = readToolName(event);
  const input = toolInputForEvent(event);

  if (event.kind === "tool_call" && input) {
    if (toolName === "Bash" && typeof input.command === "string") {
      return truncate(collapseWhitespace(input.command), 240);
    }
    if (isFileEditToolName(toolName)) {
      const path = toolFilePath(input);
      if (path) {
        return path;
      }
    }
    if (toolName === "Read" && typeof input.file_path === "string") {
      const range =
        typeof input.offset === "number" || typeof input.limit === "number"
          ? ` · ${typeof input.offset === "number" ? input.offset : 1}..${
              typeof input.limit === "number"
                ? (typeof input.offset === "number" ? input.offset : 0) + input.limit
                : "end"
            }`
          : "";
      return `${input.file_path}${range}`;
    }
    if (toolName === "Grep" && typeof input.pattern === "string") {
      const path = typeof input.path === "string" ? input.path : "";
      return path ? `${input.pattern}  ·  ${path}` : input.pattern;
    }
    if (toolName === "Glob" && typeof input.pattern === "string") {
      return input.pattern;
    }
    if ((toolName === "WebFetch" || toolName === "WebSearch") && typeof input.url === "string") {
      return input.url;
    }
    if (toolName === "WebSearch" && typeof input.query === "string") {
      return input.query;
    }
    if ((toolName === "Task" || toolName === "Agent") && typeof input.description === "string") {
      return input.description;
    }
    if (toolName === "TodoWrite" && Array.isArray(input.todos)) {
      return `${input.todos.length} todo${input.todos.length === 1 ? "" : "s"}`;
    }
    const generic = summarizeStructuredInput(input);
    if (generic) {
      return truncate(generic, 240);
    }
  }

  // Fall back to the raw event text, minus a leading tool-name line.
  return summarizeToolText(event.text, toolLabel);
}

// A one-line `key: value` preview for tool calls with no dedicated branch above
// (Skill, ToolSearch, custom MCP tools), so the arguments show and not the bare "{"
// a line-based fallback would produce for pretty-printed JSON.
function summarizeStructuredInput(input: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(input)) {
    parts.push(`${key}: ${formatStructuredValue(value)}`);
  }
  return parts.join(", ");
}

function formatStructuredValue(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return collapseWhitespace(JSON.stringify(value));
  } catch {
    return "";
  }
}

function summarizeToolText(text: string, toolLabel?: string): string {
  let lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (toolLabel && lines.length > 0 && lines[0].toLowerCase() === toolLabel.toLowerCase()) {
    lines = lines.slice(1);
  }
  if (!lines.length) {
    return "No output";
  }
  // Pretty-printed JSON puts the opening brace on its own line, which would read
  // as a useless "{ · N lines". A body that parses as JSON becomes a one-line
  // key/value summary.
  const joined = lines.join("\n");
  if (joined.startsWith("{") || joined.startsWith("[")) {
    try {
      const parsed = JSON.parse(joined);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const generic = summarizeStructuredInput(parsed as Record<string, unknown>);
        if (generic) return truncate(generic, 240);
      }
      return truncate(collapseWhitespace(JSON.stringify(parsed)), 240);
    } catch {
      // Fall through to the line-based summary.
    }
  }
  const first = lines[0];
  const suffix = lines.length > 1 ? ` · ${lines.length} lines` : "";
  return `${truncate(first, 160)}${suffix}`;
}
