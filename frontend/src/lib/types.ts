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
  // The account/config profile the session launched under (and resumes under
  // across a switch); null when the backend hosts no profiles.
  account_profile_id?: string | null;
  account_profile_label?: string | null;
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

// Redacted account/config-profile metadata. Never carries the config-dir path,
// expected account key, or transcript policy — display id/label plus the
// config-dir env key the profile maps to.
export interface AccountProfile {
  id: string;
  label: string;
  config_dir_key: string;
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
  terminal_key_injection: boolean;
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
  terminal_key_injection: boolean;
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
  default_launch_env?: Record<string, string>;
  // Redacted account/config profiles this agent hosts (claude_code, codex);
  // empty for backends that don't. Target-merged variants live on `/api/me`.
  account_profiles?: AccountProfile[];
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
  session_presets?: SessionPresetSummary[];
  default_preset_id?: string | null;
}

// Full preset spec (with launch_env values); returned only from the
// include_secret_values single-preset fetch and used to hydrate the form.
// cwd and title are intentionally absent — they are per-launch specifics, not
// reusable launch defaults, so the launch surfaces always supply them directly.
export interface SessionPresetSpec {
  backend?: Backend | null;
  launch_target_id?: string | null;
  launch_mode?: LaunchMode | null;
  transport?: SessionTransport | null;
  args?: string[];
  config_overrides?: string[];
  launch_env?: Record<string, string>;
  permission_mode?: string | null;
  model?: string | null;
  effort?: string | null;
  tags?: Record<string, string>;
  account_profile_id?: string | null;
}

// Redacted spec used on list / bootstrap surfaces: env values are omitted,
// only the keys are exposed.
export interface SessionPresetSpecSummary {
  backend?: Backend | null;
  launch_target_id?: string | null;
  launch_mode?: LaunchMode | null;
  transport?: SessionTransport | null;
  args?: string[];
  config_overrides?: string[];
  launch_env_keys?: string[];
  permission_mode?: string | null;
  model?: string | null;
  effort?: string | null;
  tags?: Record<string, string>;
  account_profile_id?: string | null;
}

export interface SessionPresetSummary {
  id: string;
  name: string;
  description?: string | null;
  spec: SessionPresetSpecSummary;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface SessionPreset {
  id: string;
  name: string;
  description?: string | null;
  spec: SessionPresetSpec;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface SessionPresetWriteRequest {
  name?: string;
  description?: string | null;
  spec?: SessionPresetSpec;
  is_default?: boolean;
}

// GET /api/sessions/{id}/launch-settings — a session's restart-applied launch
// settings (env redacted to keys). Drives the running-session profile switch.
export interface SessionLaunchSettings {
  backend: Backend;
  transport: SessionTransport;
  launch_target_id?: string | null;
  account_profile_id?: string | null;
  account_profile_label?: string | null;
  account_profiles: AccountProfile[];
  args: string[];
  config_overrides: string[];
  launch_env_keys: string[];
  supports_custom_args: boolean;
  supports_config_overrides: boolean;
  supports_account_profile_with_restart: boolean;
}

// PATCH body for the same endpoint; omitted fields are left unchanged. `restart`
// must be true to switch a running session (phase 1).
export interface LaunchSettingsUpdate {
  account_profile_id?: string | null;
  args?: string[];
  config_overrides?: string[];
  env_set?: Record<string, string>;
  env_unset?: string[];
  restart: boolean;
}

export interface EventsPage {
  events: EventRecord[];
  has_more: boolean;
  // The session's latest todo/task snapshot, sent only for the tail page so
  // the task dock survives a todo update that predates the loaded window.
  latest_todo: EventRecord | null;
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
  default_launch_env_by_backend?: Record<Backend, Record<string, string>>;
  // Target-merged account profiles keyed by agent backend id; only backends
  // that host profiles appear.
  account_profiles_by_backend?: Record<Backend, AccountProfile[]>;
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
  account_profile_id?: string | null;
  account_profile_label?: string | null;
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
  launch_env?: Record<string, string>;
  initial_prompt?: string | null;
  permission_mode?: string | null;
  model?: string | null;
  effort?: string | null;
  delay_seconds?: number | null;
  scheduled_at?: string | null;
  // Provenance only — the frontend submits already-resolved fields; sending the
  // selected preset id lets the server stamp it onto the scheduled record.
  preset_id?: string | null;
  account_profile_id?: string | null;
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

export type MessageScheduleStatus = "pending" | "sent" | "cancelled" | "failed";

export interface MessageSchedule {
  id: string;
  session_id: string;
  text: string;
  submit: boolean;
  command?: SessionCommandInvocation | null;
  items?: unknown[] | null;
  attachments?: string[] | null;
  scheduled_at?: string | null;
  status: MessageScheduleStatus;
  created_at: string;
  failure_reason?: string | null;
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
    | "side_question"
    | "inbox_update";
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

// Lead-initiated human-checkpoint inbox. Field names mirror the wire
// (snake_case), like the rest of the API surface.
export type InboxStatus = "open" | "resolved";
export type InboxBlockType = "markdown" | "attachment" | "question" | "approval";

export interface InboxAttachmentRef {
  session_id: string;
  attachment_id: string;
  // Denormalized by the backend at post/submit time so the name renders inline
  // without a per-session lookup; null for an unresolvable ref (or legacy rows).
  filename?: string | null;
  kind?: AttachmentKind | null;
}

export interface InboxReply {
  notes: string | null;
  attachments: InboxAttachmentRef[];
  created_at: string;
}

export interface InboxQuestionAnswer {
  selected: string[];
  other: string | null;
}

export interface InboxApprovalAnswer {
  decision: string;
}

export interface InboxQuestionOption {
  label: string;
  description?: string | null;
}

interface InboxBlockBase {
  id: string;
  reply: InboxReply | null;
}

export interface InboxMarkdownBlock extends InboxBlockBase {
  type: "markdown";
  text: string;
}

export interface InboxAttachmentBlock extends InboxBlockBase {
  type: "attachment";
  ref: InboxAttachmentRef;
}

export interface InboxQuestionBlock extends InboxBlockBase {
  type: "question";
  header: string | null;
  question: string;
  options: InboxQuestionOption[];
  multi: boolean;
  required: boolean;
  answer: InboxQuestionAnswer | null;
  answered_at: string | null;
}

export interface InboxApprovalBlock extends InboxBlockBase {
  type: "approval";
  prompt: string;
  options: string[];
  required: boolean;
  answer: InboxApprovalAnswer | null;
  answered_at: string | null;
}

export type InboxBlock =
  | InboxMarkdownBlock
  | InboxAttachmentBlock
  | InboxQuestionBlock
  | InboxApprovalBlock;

export interface InboxItem {
  id: string;
  from_session_id: string;
  from_label: string | null;
  subject: string;
  status: InboxStatus;
  read_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  blocks: InboxBlock[];
}
