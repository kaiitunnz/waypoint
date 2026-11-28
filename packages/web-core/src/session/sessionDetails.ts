import { displayAgentFor, humaniseBackend, transportLabel, type BackendCatalog } from "../backends";
import { effortLabel, formatResolvedModelLabel } from "../modelDisplay";
import { serverNow } from "../serverClock";
import { shortModelLabel } from "../sessionPresentation";
import type { BackendModelOption, SessionRecord } from "../types";

export type DetailId =
  | "session"
  | "thread"
  | "folder"
  | "agent"
  | "interface"
  | "model"
  | "effort"
  | "profile"
  | "host"
  | "source"
  | "created"
  | "worktree"
  | "branch";

// A machine string (an id, a branch) is mono and cut from the left when long; a path is
// cut from the left at a folder; a word or a date is plain.
export type DetailKind = "id" | "path" | "text";

export interface DetailRow {
  id: DetailId;
  label: string;
  value: string;
  kind: DetailKind;
  // The value is worth copying.
  copy: boolean;
  // The model was chosen but the agent has not replied under it yet.
  note?: "unconfirmed";
  // The full value, where the shown one is a label for it.
  title?: string;
}

export interface SessionDetailsOptions {
  catalog?: BackendCatalog;
  // The SSH target's name; the raw target id stands in without it.
  launchTargetName?: string | null;
  // The branch the workspace is on now. `session.branch` is only the one it launched on.
  liveBranch?: string | null;
  modelOptions?: BackendModelOption[];
}

// Where the agent's own thread lives, for the agents that keep one.
export function threadIdOf(session: SessionRecord): string | null {
  const thread = session.transport_state?.thread_id;
  return typeof thread === "string" && thread ? thread : null;
}

export function isManaged(session: SessionRecord): boolean {
  return session.source === "managed";
}

const SOURCE_LABEL: Record<SessionRecord["source"], string> = {
  managed: "Managed",
  attached_tmux: "Attached",
  assistant: "Assistant",
  telemetry: "Attached",
};

function formatCreated(iso: string): string {
  const at = new Date(iso);
  if (!Number.isFinite(at.getTime())) return "";
  const sameYear = at.getFullYear() === new Date(serverNow()).getFullYear();
  const day = at.toLocaleDateString("en", sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
  const time = `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
  return `${day}, ${time}`;
}

function modelRow(session: SessionRecord, modelOptions: BackendModelOption[]): DetailRow | null {
  const { resolved_model: resolved, model } = session;
  if (resolved) {
    const value = formatResolvedModelLabel(resolved, model, modelOptions);
    return value ? { id: "model", label: "Model", value, kind: "text", copy: false, title: resolved } : null;
  }
  const value = shortModelLabel(session);
  return value ? { id: "model", label: "Model", value, kind: "text", copy: false, note: "unconfirmed", title: model ?? undefined } : null;
}

// What the inspector's Details lists for a session, in order; a fact the session lacks
// has no row.
export function sessionDetails(session: SessionRecord, options: SessionDetailsOptions = {}): DetailRow[] {
  const { catalog, launchTargetName, liveBranch, modelOptions = [] } = options;
  const assistant = session.source === "assistant";
  const thread = threadIdOf(session);
  const profile = session.account_profile_label;
  const branch = liveBranch || session.branch;
  const rows: (DetailRow | null)[] = [
    { id: "session", label: "Session", value: session.id, kind: "id", copy: true },
    thread ? { id: "thread", label: "Thread", value: thread, kind: "id", copy: true } : null,
    !assistant && session.cwd ? { id: "folder", label: "Folder", value: session.cwd, kind: "path", copy: true } : null,
    { id: "agent", label: "Agent", value: humaniseBackend(displayAgentFor(session.backend, session.transport, catalog), catalog), kind: "text", copy: false },
    assistant ? null : { id: "interface", label: "Interface", value: transportLabel(session.transport, catalog), kind: "text", copy: false },
    modelRow(session, modelOptions),
    session.effort ? { id: "effort", label: "Effort", value: effortLabel(session.effort), kind: "text", copy: false } : null,
    profile ? { id: "profile", label: "Profile", value: profile, kind: "text", copy: false } : null,
    { id: "host", label: "Host", value: launchTargetName || session.launch_target_id || "Local", kind: "text", copy: false },
    { id: "source", label: "Source", value: SOURCE_LABEL[session.source], kind: "text", copy: false },
    formatCreated(session.created_at) ? { id: "created", label: "Created", value: formatCreated(session.created_at), kind: "text", copy: false } : null,
    session.worktree_path ? { id: "worktree", label: "Worktree", value: session.worktree_path, kind: "path", copy: true } : null,
    branch ? { id: "branch", label: "Branch", value: branch, kind: "id", copy: false } : null,
  ];
  return rows.filter((row): row is DetailRow => row !== null);
}
