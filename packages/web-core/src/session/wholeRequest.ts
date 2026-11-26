// A pending approval as a screen that decides it shows it: the whole request, or a lock.

import { asRecord, readToolName, type EventDiffPreview } from "../events";
import { hunksAddUp } from "../highlight";
import type { EventRecord } from "../types";
import { resolveFromCwd } from "../workspacePaths";
import { approvalBody, WORKFLOW_TOOLS, type ApprovalBody } from "./approvals";
import { isBareRename } from "./diffPreview";

// What reading a session's pending request needs to know of the session.
export interface AskSession {
  cwd: string;
  // The agent's dialogs are read off its screen: see `readsDialogsFromScreen`.
  fromScreen: boolean;
}

export interface ShownRequest {
  // The typed view.
  body: ApprovalBody;
  // Every field of the request, shown raw below the view.
  fields: Record<string, unknown>;
  // What the request asks for beyond the call it names, or why it cannot be decided.
  warnings: string[];
  // Read off the agent's screen, so what it names may not be what is asked.
  unverified: boolean;
  // All of what is asked is shown, so it can be decided on.
  whole: boolean;
}

// ─── Hidden text ───

// Characters that draw nothing or reorder the text around them; line breaks and tabs
// are not among them.
const HIDDEN = /(?![\n\t]|\r\n)[\p{Cc}\p{Cf}\u2028\u2029]/gu;

export const hasHiddenText = (text: string) => text.search(HIDDEN) !== -1;

const escaped = (character: string) =>
  Array.from({ length: character.length }, (_, i) => `\\u${character.charCodeAt(i).toString(16).padStart(4, "0")}`).join("");

// The whole request as JSON, with hidden characters written out.
export const requestText = (fields: Record<string, unknown>) => JSON.stringify(fields, null, 2).replace(HIDDEN, escaped);

const strings = (value: unknown): string[] => {
  if (typeof value === "string") return [value];
  return value && typeof value === "object" ? Object.values(value).flatMap(strings) : [];
};

const HIDDEN_WARNING = "Hidden or direction-changing characters";

// ─── The view ───

// A request that carries its command beside its title shows the command.
function requestedBody(event: EventRecord): ApprovalBody {
  const body = approvalBody(event);
  if (body.kind !== "raw" || asRecord(event.metadata?.tool_input)) return body;
  const command = asRecord(event.metadata?.request)?.command;
  return typeof command === "string" && command ? { kind: "command", description: "", command } : body;
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
    [HIDDEN_WARNING, strings(event.metadata).some(hasHiddenText)],
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
    // A script named by file or by name runs in place of the one given.
    case "workflow":
      return body.script.trim() !== "" && !input?.scriptPath && !input?.name;
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

// A plan with no call behind it: its text is the request.
export function shownPlan(plan: string): ShownRequest {
  return {
    body: { kind: "plan", plan },
    fields: { plan },
    warnings: hasHiddenText(plan) ? [HIDDEN_WARNING] : [],
    unverified: false,
    whole: plan.trim() !== "",
  };
}

const UNVERIFIED = "This call can't be verified from here";

// A pending request as read from its transcript. A dialog read off the screen may name
// another call than the one a reply would answer, so nothing there is decided.
export function shownRequest(request: EventRecord, session: AskSession): ShownRequest {
  const body = requestedBody(request);
  const fields = requestFields(request);
  // A plan comes from its plan file, not off the screen.
  if (session.fromScreen && asRecord(request.metadata?.interaction)?.kind !== "plan_approval") {
    return { body, fields, warnings: [UNVERIFIED], unverified: true, whole: false };
  }
  return {
    body,
    fields,
    warnings: requestWarnings(request, session.cwd),
    unverified: false,
    whole: holdsContent(body, asRecord(request.metadata?.tool_input), readToolName(request)),
  };
}
