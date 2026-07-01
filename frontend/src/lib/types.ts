// Plugin-supplied backends register at runtime, so these are arbitrary
// strings rather than a closed union; the frontend looks up labels,
// badges, capabilities, etc. via `useBackendCatalog()` instead of
// hand-mirroring per-backend constants.
export type Backend = string;
export type SessionTransport = string;
export type SessionSource = "managed" | "attached_tmux" | "assistant";
export type LaunchMode = "auto" | "direct" | "tmux_wrapper";
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

export interface SessionContextUsage {
  used_tokens: number;
  context_window_tokens?: number | null;
  updated_at: string;
  source: Backend;
  breakdown?: Record<string, number>;
}

export interface UsageWindow {
  id: string;
  label: string;
  used_percent: number;
  used_tokens?: number | null;
  limit_tokens?: number | null;
  remaining_tokens?: number | null;
  window_minutes?: number | null;
  resets_at?: string | null;
  reset_description?: string | null;
}

export interface SessionRateLimitUsage {
  source: Backend;
  updated_at: string;
  windows: UsageWindow[];
  credits_remaining?: number | null;
  credits_currency?: string | null;
  notes?: string[];
}

export interface UsageDashboardBucket {
  backend: Backend;
  account_key: string;
  account_label: string;
  snapshot: SessionRateLimitUsage;
  session_ids: string[];
}

export interface UsageDashboardResponse {
  buckets: UsageDashboardBucket[];
}

export interface SessionRecord {
  id: string;
  backend: Backend;
  source: SessionSource;
  transport: SessionTransport;
  title: string;
  cwd: string;
  launch_target_id?: string | null;
  launch_mode?: LaunchMode;
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
  // The concrete model id the backend actually resolved and ran (e.g.
  // "claude-sonnet-5"), as reported at launch time. Distinct from `model`,
  // which is the user's selection (e.g. the alias "sonnet") and is what
  // relaunch/set-model send. Prefer this for display when present.
  resolved_model?: string | null;
  effort?: string | null;
  context_usage?: SessionContextUsage | null;
  rate_limit_usage?: SessionRateLimitUsage | null;
  args: string[];
  config_overrides: string[];
}

export type AttachmentKind = "image" | "file";

export interface AttachmentSpec {
  id: string;
  filename: string;
  mime: string;
  size: number;
  kind: AttachmentKind;
}

// A session-stored attachment as returned by the list endpoint: the spec plus
// the upload time (epoch seconds), so the files manager can sort newest-first.
export interface SessionAttachment extends AttachmentSpec {
  uploaded_at: number;
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

export type CompletionDispatch =
  | "frontend_control"
  | "plain_text"
  | "backend_command"
  | "structured_skill";

export interface CommandCompletion {
  id: string;
  trigger: string;
  replacement: string;
  name: string;
  description?: string | null;
  kind: string;
  source: string;
  dispatch: CompletionDispatch;
  argument_hint?: string | null;
  metadata: Record<string, unknown>;
}

export interface SessionCompletionsResponse {
  completions: CommandCompletion[];
  refreshing: boolean;
}

export interface SessionCommandInvocation {
  completion_id: string;
  name: string;
  arguments: string;
  dispatch: CompletionDispatch;
  metadata: Record<string, unknown>;
}

// A session is an (agent, transport) pair. The backend splits the flat
// capability descriptor along that seam: agent-level fields (model source,
// permission modes, slash commands, fork/thread support) come from the
// AgentPlugin; transport-level fields (structured vs heuristic, resume,
// live terminal) come from the Transport. `BackendCapabilities` keeps the
// flat union for callers that only have a single descriptor.

export interface TransportCapabilities {
  is_structured: boolean;
  supports_resume: boolean;
  supports_reattach_after_exit: boolean;
  supports_terminate: boolean;
  supports_set_model_inline: boolean;
  supports_set_effort_inline: boolean;
  supports_set_effort_with_restart: boolean;
  supports_set_permission_mode_inline: boolean;
  settings_change_interrupts_turn: boolean;
  live_terminal: boolean;
  has_terminal_pane: boolean;
  terminal_interactive: boolean;
  terminal_resizable: boolean;
  is_fallback_for_managed_launch: boolean;
}

export interface AgentCapabilities {
  model_source: "static" | "live_rpc" | "none";
  permission_modes: BackendPermissionMode[];
  effort_levels: string[];
  slash_commands: BackendSlashCommand[];
  approval_decisions: string[];
  supports_thread_discovery: boolean;
  supports_thread_import: boolean;
  supports_thread_delete: boolean;
  supports_fork: boolean;
  supports_plan_approval: boolean;
  supports_approval_note: boolean;
  supports_attachments: boolean;
  supports_custom_cli_args: boolean;
  supports_config_overrides: boolean;
  supports_slash_compact: boolean;
  cli_binary?: string | null;
  target_aliases: string[];
  badges: Record<string, string>;
}

export interface BackendCapabilities {
  is_structured: boolean;
  supports_resume: boolean;
  supports_terminate: boolean;
  supports_set_model_inline: boolean;
  supports_set_effort_inline: boolean;
  supports_set_effort_with_restart: boolean;
  supports_set_permission_mode_inline: boolean;
  settings_change_interrupts_turn: boolean;
  live_terminal: boolean;
  has_terminal_pane: boolean;
  terminal_interactive: boolean;
  terminal_resizable: boolean;
  supports_thread_discovery: boolean;
  supports_thread_import: boolean;
  supports_thread_delete: boolean;
  supports_fork: boolean;
  supports_plan_approval: boolean;
  supports_slash_compact: boolean;
  supports_approval_note: boolean;
  supports_attachments: boolean;
  supports_custom_cli_args: boolean;
  supports_config_overrides: boolean;
  supports_reattach_after_exit: boolean;
  model_source: "static" | "live_rpc" | "none";
  approval_decisions: string[];
  effort_levels: string[];
  permission_modes: BackendPermissionMode[];
  slash_commands: BackendSlashCommand[];
  cli_binary?: string | null;
  target_aliases: string[];
  is_fallback_for_managed_launch: boolean;
}

export interface BackendDescriptor {
  id: Backend;
  transport_id: SessionTransport;
  // Every transport this agent can be driven over (its native one plus any
  // folded-in alternatives such as `claude_tty` or the generic `tmux` pane).
  // The launch picker offers these and defaults to `default_transport`.
  supported_transports: SessionTransport[];
  default_transport: SessionTransport;
  label: string;
  badges: Record<string, string>;
  // The flat union, kept for back-compat; prefer composing `agent_capabilities`
  // with `transport_capabilities` via `capsFor()` for an (agent, transport) pair.
  capabilities: BackendCapabilities;
  agent_capabilities: AgentCapabilities;
  transport_capabilities: TransportCapabilities;
}

export interface AssistantSummary {
  session_id: string;
  backend: Backend;
  // Transport the assistant runs over (Chat / Emulated / Terminal); lets the
  // UI label it and preselect it in the settings popover.
  transport: SessionTransport;
  native_thread_id: string | null;
  status: SessionStatus;
  // Whether the backend can revive the thread after it exits — drives the
  // choice between offering Reattach and only Clear context.
  supports_reattach: boolean;
}

export interface AssistantResetRequest {
  backend?: Backend;
  transport?: SessionTransport | null;
  model?: string | null;
  effort?: string | null;
  permission_mode?: string | null;
}

export interface AssistantAttachRequest {
  backend: Backend;
  thread_id: string;
  launch_target_id?: string | null;
}

export interface MeResponse {
  authenticated: boolean;
  default_backend: Backend;
  default_cwd: string;
  launch_targets: LaunchTargetSummary[];
  backends?: BackendDescriptor[];
  assistant?: AssistantSummary | null;
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
  auth?: "key" | "password";
  connected?: boolean;
}

export type ScheduleStatus = "pending" | "launched" | "cancelled" | "failed";

export interface ScheduledSession {
  id: string;
  backend: Backend;
  cwd: string;
  launch_target_id?: string | null;
  launch_mode: LaunchMode;
  transport?: SessionTransport | null;
  title?: string | null;
  args: string[];
  config_overrides?: string[];
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
  launch_mode?: LaunchMode;
  // Pins the transport the scheduled session is driven over; an explicit
  // transport supersedes launch_mode when the schedule fires.
  transport?: SessionTransport | null;
  title?: string | null;
  args?: string[];
  config_overrides?: string[];
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

export interface BoardEntry {
  id: number;
  channel: string;
  author_session_id?: string | null;
  key?: string | null;
  text: string;
  metadata: Record<string, unknown>;
  created_at: string;
  edited_at?: string | null;
}

export interface BoardChannel {
  channel: string;
  entry_count: number;
  last_created_at: string;
}

export interface SessionEnvelope {
  type:
    | "session_list_update"
    | "event"
    | "session_state"
    | "auth_revoked"
    | "schedule_list_update"
    | "board_update"
    | "clipboard_copy"
    | "side_question";
  payload: Record<string, unknown>;
}

export type SideQuestionStatus = "pending" | "answered" | "error";

// An ephemeral /btw side-question. Mirrors backend schemas.SideQuestion. The
// `side_question` envelope carries either an upsert ({ side_question })
// or a removal ({ removed_id }).
export interface SideQuestion {
  id: string;
  question: string;
  status: SideQuestionStatus;
  answer?: string | null;
  error?: string | null;
  fork_thread_id?: string | null;
  attempts: number;
  resumed: boolean;
  created_at: string;
}
