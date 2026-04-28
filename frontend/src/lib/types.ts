export type Backend = "claude_code" | "codex";
export type SessionSource = "managed" | "attached_tmux";
export type SessionTransport = "tmux" | "codex_app_server";
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
  remote_cwd?: string | null;
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
  remote_codex_enabled: boolean;
  default_remote_cwd?: string | null;
}

export interface SessionEnvelope {
  type: "session_list_update" | "event" | "session_state" | "terminal_snapshot_ready" | "auth_revoked";
  payload: Record<string, unknown>;
}
