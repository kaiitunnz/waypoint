// A pending approval as a screen that decides it shows it: the whole request, or a lock.

import { asRecord, FILE_EDIT_TOOLS, parseEvent, readToolName, toolFilePath, type EventDiffPreview } from "../events";
import { hunksAddUp } from "../highlight";
import type { EventRecord } from "../types";
import { resolveFromCwd, splitPath } from "../workspacePaths";
import { approvalBody, WORKFLOW_TOOLS, type ApprovalBody } from "./approvals";
import { isBareRename } from "./diffPreview";
import { toolInputForEvent } from "./toolPresentation";

// What reading a session's pending request needs to know of the session.
export interface AskSession {
  cwd: string;
  // The agent's dialogs are read off its screen: see `readsDialogsFromScreen`.
  fromScreen: boolean;
  // Changes when the session records something.
  revision: string;
}

export interface ShownRequest {
  // The typed view.
  body: ApprovalBody;
  // Every field of the request, shown raw below the view; null where the view is all of it.
  fields: Record<string, unknown> | null;
  // What the request asks for beyond the call it names.
  warnings: string[];
  // The call's own input; null where it is not known.
  input: Record<string, unknown> | null;
  // All of what is asked is shown, so it can be decided on.
  whole: boolean;
}

// ─── The call behind a dialog ───

const callId = (event: EventRecord) => event.metadata?.tool_use_id ?? event.metadata?.item_id;

const fileName = (path: string | null) => (path === null ? null : splitPath(path).base);

// A pane wraps a command into rows of its own and pads them.
const collapse = (value: unknown) => (typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "");

const canonical = (value: unknown): string =>
  JSON.stringify(value, (_, entry: unknown) => {
    const record = asRecord(entry);
    return record ? Object.fromEntries(Object.entries(record).sort(([a], [b]) => a.localeCompare(b))) : entry;
  });

// The input of the transcript's call that a dialog read off the screen stands for, only
// when certain. The candidates are the unanswered calls of its tool since the human last
// spoke: a command's are those whose command reads as the dialog's does, and they must
// all carry one input; a file's is the single latest one of that very file.
function callBehind(request: EventRecord, events: readonly EventRecord[], cwd: string): Record<string, unknown> | null {
  const tool = readToolName(request);
  const asked = asRecord(request.metadata?.tool_input);
  // A subagent's own dialog reads as its parent's launch, so a launch is never matched.
  if (!tool || !asked || tool === "Task" || tool === "Agent") return null;
  const spoke = events.filter((event) => event.kind === "user_input" && event.sequence < request.sequence);
  const turn = events.filter((event) => event.sequence > Math.max(-1, ...spoke.map((event) => event.sequence)));
  const answered = new Set(turn.filter((event) => event.kind === "tool_result").map(callId));
  const calls = turn.filter((event) => event.kind === "tool_call" && readToolName(event) === tool);
  // A call without an id cannot be shown to be unanswered.
  if (calls.some((event) => !callId(event))) return null;
  const open = (candidates: EventRecord[]) => candidates.filter((event) => !answered.has(callId(event)));

  if (FILE_EDIT_TOOLS.has(tool)) {
    // The dialog may name the file without its folder.
    const path = resolveFromCwd(toolFilePath(asked) ?? "", cwd);
    if (!toolFilePath(asked) || !path) return null;
    const named = calls.filter((event) => fileName(toolFilePath(toolInputForEvent(event))) === fileName(path));
    const pending = open(named);
    if (pending.length !== 1 || pending[0] !== named[named.length - 1]) return null;
    const input = toolInputForEvent(pending[0]);
    return toolFilePath(input) === path ? input : null;
  }

  const command = collapse(asked.command);
  if (tool === "Bash" && !command) return null;
  const pending = open(
    tool === "Bash" ? calls.filter((event) => collapse(toolInputForEvent(event)?.command) === command) : calls,
  );
  const inputs = pending.map(toolInputForEvent);
  const [input] = inputs;
  return input && inputs.every((other) => canonical(other) === canonical(input)) ? input : null;
}

// ─── The view ───

// A request that carries its command beside its title shows the command.
function requestedBody(event: EventRecord): ApprovalBody {
  const body = approvalBody(event);
  if (body.kind !== "raw" || asRecord(event.metadata?.tool_input)) return body;
  const command = asRecord(event.metadata?.request)?.command;
  return typeof command === "string" && command ? { kind: "command", description: "", command } : body;
}

// A write replaces its file, so its content is the change.
function writtenBody(event: EventRecord): ApprovalBody | null {
  if (parseEvent(event).diffPreview || readToolName(event) !== "Write") return null;
  const input = asRecord(event.metadata?.tool_input);
  const path = toolFilePath(input);
  const content = input?.content;
  if (!path || typeof content !== "string" || !content) return null;
  const lines = content.replace(/\n$/, "").split("\n");
  const diff = [`@@ -0,0 +1,${lines.length} @@`, ...lines.map((line) => `+${line}`), ""].join("\n");
  const file = { path, changeType: "unknown" as const, diff, additions: lines.length, deletions: 0 };
  const preview: EventDiffPreview = {
    schemaVersion: 1,
    phase: "proposed",
    files: [{ ...file, truncated: false, binary: false }],
    totalAdditions: lines.length,
    totalDeletions: 0,
    truncated: false,
  };
  return { kind: "diff", prompt: event.text, preview };
}

// Metadata that routes or draws a request without being part of it.
const ENVELOPE = new Set([
  "approval",
  "approval_id",
  "dialog_signature",
  "diff_preview",
  "interaction",
  "item_id",
  "method",
  "status",
  "tool_use_id",
  "version",
]);

// Everything else an agent attached, new fields included, under the title it gave.
function requestFields(event: EventRecord): Record<string, unknown> {
  const title = asRecord(event.metadata?.interaction)?.title;
  const fields = Object.entries(event.metadata ?? {}).filter(([key]) => !ENVELOPE.has(key));
  return Object.fromEntries(typeof title === "string" ? [["title", title], ...fields] : fields);
}

const isSet = (value: unknown) => Boolean(value) && !(Array.isArray(value) && value.length === 0);

function requestWarnings(event: EventRecord, cwd: string): string[] {
  const input = asRecord(event.metadata?.tool_input) ?? {};
  const request = asRecord(event.metadata?.request) ?? {};
  const edits = Array.isArray(input.edits) ? input.edits.map(asRecord) : [];
  const home = resolveFromCwd(cwd, "");
  const elsewhere = [input.cwd, input.workdir, request.cwd].some(
    (dir) => typeof dir === "string" && dir !== "" && (!home || resolveFromCwd(dir, cwd) !== home),
  );
  const permissions = [
    input.additionalPermissions,
    input.allowedPrompts,
    request.additionalPermissions,
    request.networkApprovalContext,
    request.permissions,
  ];
  const everywhere = [input, ...edits].some((edit) => isSet(edit?.replace_all) || isSet(edit?.replaceAll));
  const checks: [string, boolean][] = [
    ["Runs outside the sandbox", isSet(input.dangerouslyDisableSandbox)],
    ["Extra permissions or network access", permissions.some(isSet)],
    ["Write access for the whole session", isSet(input.grantRoot) || isSet(request.grantRoot)],
    ["Replaces every occurrence", everywhere],
    ["Different working directory", isSet(input.isolation) || elsewhere],
  ];
  return checks.filter(([, applies]) => applies).map(([warning]) => warning);
}

// ─── Whole or locked ───

// Tools that ask to run a text of their own (a plan, prompt, command or script): without
// it, what is asked is not on screen.
const CONTENT_TOOLS = new Set(["ExitPlanMode", "Task", "Agent", "Bash", ...WORKFLOW_TOOLS]);

// Lines show only under a numbered hunk header, and a diff whose hunks do not add up
// shows something other than the change.
const hasWholeDiff = (file: EventDiffPreview["files"][number]) =>
  !file.truncated &&
  !file.binary &&
  !file.unavailableReason &&
  (isBareRename(file) || (/^@@ -\d/m.test(file.diff) && hunksAddUp(file.diff)));

// Whether a view holds the content of what is asked, `input` and `tool` being the call's
// own. Nobody decides without it: a plan, prompt or command with no text, an edit without
// its change, a diff that is cut or lists a file without it, or a request known only by
// its title or method.
function holdsContent(body: ApprovalBody, input: Record<string, unknown> | null, tool: string | null): boolean {
  switch (body.kind) {
    case "plan":
      return body.plan.trim() !== "";
    case "task":
      return body.prompt.trim() !== "";
    case "command":
      return body.command.trim() !== "";
    case "workflow":
      return body.script.trim() !== "";
    case "diff":
      return !body.preview.truncated && body.preview.files.length > 0 && body.preview.files.every(hasWholeDiff);
    // The path alone: the change is what is asked.
    case "edit":
      return false;
    case "raw": {
      if (!input || (tool && CONTENT_TOOLS.has(tool))) return false;
      // A call whose every argument came through empty shows nothing of itself.
      const values = Object.values(input);
      return values.length === 0 || values.some((value) => value !== "");
    }
  }
}

// A pending request as read from its transcript; `events` are those around it, up to the
// present. Where the dialog was read off the screen, only the transcript's call says what
// is asked, and without one the request is locked.
export function shownRequest(
  request: EventRecord,
  events: readonly EventRecord[],
  session: Pick<AskSession, "cwd" | "fromScreen">,
): ShownRequest {
  // A plan comes from its plan file, not off the screen.
  const screen = session.fromScreen && readToolName(request) !== "ExitPlanMode";
  const call = screen ? callBehind(request, events, session.cwd) : null;
  const event = call ? { ...request, metadata: { ...request.metadata, tool_input: call } } : request;
  const input = asRecord(event.metadata?.tool_input);
  const known = !screen || call !== null;
  const body = writtenBody(event) ?? requestedBody(event);
  return {
    body,
    fields: requestFields(event),
    warnings: requestWarnings(event, session.cwd),
    input: known ? input : null,
    whole: known && holdsContent(body, input, readToolName(event)),
  };
}
