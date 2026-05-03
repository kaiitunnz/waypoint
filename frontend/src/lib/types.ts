// Plugin-supplied backends register at runtime, so these are arbitrary
// strings rather than a closed union; the frontend looks up labels,
// badges, capabilities, etc. via `useBackendCatalog()` instead of
// hand-mirroring per-backend constants.
export type Backend = string;
export type SessionTransport = string;
export type SessionSource = "managed" | "attached_tmux";
export type SessionStatus =
  | "starting"
  | "idle"
  | "waiting_input"
  | "running"
  | "interrupted"
  | "exited"
  | "error";
export type EventKind =
  | "user_input"
  | "agent_output"
  | "tool_call"
  | "tool_result"
  | "approval_request"
  | "status_update"
  | "system_note"
  | "raw_terminal_chunk";

export interface SessionRecord {
  id: string;
  backend: Backend;
  source: SessionSource;
  transport: SessionTransport;
  title: string;
  cwd: string;
  launch_target_id?: string | null;
  repo_name?: string | null;
  branch?: string | null;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
  last_event_at: string;
  // Per-plugin opaque state. Common keys: `thread_id` (Codex / Claude),
  // `tmux_session` / `tmux_window` / `tmux_pane` / `pid` (tmux fallback).
  // The shape depends on which plugin owns the session — only that
  // plugin's UI code should reach into specific keys.
  transport_state: Record<string, unknown>;
  raw_log_path: string;
  structured_log_path: string;
  pinned_at?: string | null;
  permission_mode?: string | null;
  model?: string | null;
  effort?: string | null;
}

export interface EventRecord {
  id?: number;
  session_id: string;
  ts: string;
  kind: EventKind;
  text: string;
  metadata: Record<string, unknown>;
  sequence: number;
}

export interface BackendPermissionMode {
  id: string;
  label: string;
  description?: string | null;
  requires_session_restart?: boolean;
}

export interface BackendSlashCommand {
  name: string;
  description?: string | null;
}

export interface BackendCapabilities {
  is_structured: boolean;
  supports_resume: boolean;
  supports_terminate: boolean;
  supports_set_model_inline: boolean;
  supports_set_effort_inline: boolean;
  supports_set_effort_with_restart: boolean;
  supports_set_permission_mode_inline: boolean;
  supports_thread_discovery: boolean;
  supports_thread_import: boolean;
  supports_slash_compact: boolean;
  supports_approval_note: boolean;
  model_source: "static" | "live_rpc" | "none";
  approval_decisions: string[];
  effort_levels: string[];
  permission_modes: BackendPermissionMode[];
  slash_commands: BackendSlashCommand[];
  cli_binary?: string | null;
  target_aliases: string[];
}

export interface BackendDescriptor {
  id: Backend;
  transport_id: SessionTransport;
  label: string;
  badges: Record<string, string>;
  capabilities: BackendCapabilities;
}

export interface MeResponse {
  authenticated: boolean;
  default_backend: Backend;
  default_cwd: string;
  launch_targets: LaunchTargetSummary[];
  backends?: BackendDescriptor[];
}

export interface EventsPage {
  events: EventRecord[];
  has_more: boolean;
}

export interface LaunchTargetSummary {
  id: string;
  name: string;
  kind: "ssh";
  supported_backends: Backend[];
  default_backend: Backend;
  default_cwd?: string | null;
}

export type ScheduleStatus = "pending" | "launched" | "cancelled" | "failed";

export interface ScheduledSession {
  id: string;
  backend: Backend;
  cwd: string;
  launch_target_id?: string | null;
  title?: string | null;
  args: string[];
  initial_prompt?: string | null;
  permission_mode?: string | null;
  model?: string | null;
  effort?: string | null;
  scheduled_at: string;
  created_at: string;
  status: ScheduleStatus;
  session_id?: string | null;
  failure_reason?: string | null;
}

export interface ScheduleCreateRequest {
  backend: Backend;
  cwd: string;
  launch_target_id?: string | null;
  title?: string | null;
  args?: string[];
  initial_prompt?: string | null;
  permission_mode?: string | null;
  model?: string | null;
  effort?: string | null;
  delay_seconds?: number | null;
  scheduled_at?: string | null;
}

export interface BackendModelOption {
  id: string;
  label: string;
  description?: string | null;
  is_default?: boolean;
  hidden?: boolean;
  supported_efforts?: string[];
  default_effort?: string | null;
}

export interface BackendModelListResponse {
  backend: Backend;
  models: BackendModelOption[];
  default_model_id?: string | null;
  default_model_label?: string | null;
  default_effort?: string | null;
  supports_free_text?: boolean;
}

export interface SessionEnvelope {
  type:
    | "session_list_update"
    | "event"
    | "session_state"
    | "terminal_snapshot_ready"
    | "auth_revoked"
    | "schedule_list_update";
  payload: Record<string, unknown>;
}
