export type Backend = "claude_code" | "codex";
export type SessionSource = "managed" | "attached_tmux";
export type SessionTransport = "tmux" | "codex_app_server" | "claude_cli";
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
  tmux_session?: string | null;
  tmux_window?: string | null;
  tmux_pane?: string | null;
  thread_id?: string | null;
  raw_log_path: string;
  structured_log_path: string;
  pid?: number | null;
  pinned_at?: string | null;
  permission_mode?: string | null;
}

export interface CodexThreadSummary {
  id: string;
  title: string;
  cwd: string;
  repo_name?: string | null;
  branch?: string | null;
  preview?: string | null;
  created_at: string;
  updated_at: string;
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

export interface MeResponse {
  authenticated: boolean;
  default_backend: Backend;
  default_cwd: string;
  launch_targets: LaunchTargetSummary[];
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
  delay_seconds?: number | null;
  scheduled_at?: string | null;
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
