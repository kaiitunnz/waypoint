from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints


def _validate_backend_id(value: Any) -> str:
    if isinstance(value, StrEnum):
        value = str(value)
    if not isinstance(value, str) or not value:
        raise TypeError(f"backend id must be a non-empty string, got {value!r}")
    from waypoint.backends.registry import get_registry

    if not get_registry().has_backend(value):
        raise ValueError(f"unknown backend: {value!r}")
    return value


def _validate_transport_id(value: Any) -> str:
    if isinstance(value, StrEnum):
        value = str(value)
    if not isinstance(value, str) or not value:
        raise TypeError(f"transport id must be a non-empty string, got {value!r}")
    from waypoint.backends.registry import get_registry

    if not get_registry().has_transport(value):
        raise ValueError(f"unknown transport: {value!r}")
    return value


# Annotated string types validated against the live plugin registry.
# Schemas use these so adding a new backend never requires editing this
# module: any plugin registered via ``backends/bootstrap.py`` is
# automatically accepted as a valid id.
BackendId = Annotated[str, BeforeValidator(_validate_backend_id)]
SessionTransportId = Annotated[str, BeforeValidator(_validate_transport_id)]


class SessionSource(StrEnum):
    MANAGED = "managed"
    ATTACHED_TMUX = "attached_tmux"
    # The personal-assistant singleton. Created and kept alive by the
    # runtime, protected from deletion/termination via the public API,
    # and surfaced on its own UI page rather than the session list.
    ASSISTANT = "assistant"


class LaunchMode(StrEnum):
    AUTO = "auto"
    DIRECT = "direct"
    TMUX_WRAPPER = "tmux_wrapper"


class SessionStatus(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    WAITING_INPUT = "waiting_input"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    EXITED = "exited"
    ERROR = "error"


class EventKind(StrEnum):
    USER_INPUT = "user_input"
    AGENT_OUTPUT = "agent_output"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUEST = "approval_request"
    STATUS_UPDATE = "status_update"
    SYSTEM_NOTE = "system_note"
    RAW_TERMINAL_CHUNK = "raw_terminal_chunk"


class CompletionDispatch(StrEnum):
    FRONTEND_CONTROL = "frontend_control"
    PLAIN_TEXT = "plain_text"
    BACKEND_COMMAND = "backend_command"
    STRUCTURED_SKILL = "structured_skill"


class CommandCompletion(BaseModel):
    id: str
    trigger: str
    replacement: str
    name: str
    description: str | None = None
    kind: str
    source: str
    dispatch: CompletionDispatch
    argument_hint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionCommandInvocation(BaseModel):
    completion_id: str
    name: str
    arguments: str = ""
    dispatch: CompletionDispatch
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionInputItem(BaseModel):
    type: Literal["text", "skill", "mention"]
    text: str | None = None
    name: str | None = None
    path: str | None = None


class AttachmentKind(StrEnum):
    IMAGE = "image"
    FILE = "file"


class AttachmentSpec(BaseModel):
    # Server-issued handle for an uploaded blob. The frontend receives this
    # from the upload endpoint and later references the attachment by ``id``
    # when sending input; the runtime resolves the id back to a host path
    # server-side, so the absolute path is never trusted from the client.
    id: str
    filename: str
    mime: str
    size: int
    kind: AttachmentKind


class SessionCompletionsResponse(BaseModel):
    completions: list[CommandCompletion] = Field(default_factory=list)
    refreshing: bool = False


class SessionContextUsage(BaseModel):
    used_tokens: int
    context_window_tokens: int | None = None
    updated_at: datetime
    source: BackendId
    breakdown: dict[str, int] = Field(default_factory=dict)


class UsageWindow(BaseModel):
    id: str
    label: str
    used_percent: float
    used_tokens: int | None = None
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    window_minutes: int | None = None
    resets_at: datetime | None = None
    reset_description: str | None = None


class SessionRateLimitUsage(BaseModel):
    source: BackendId
    updated_at: datetime
    windows: list[UsageWindow] = Field(default_factory=list)
    credits_remaining: float | None = None
    credits_currency: str | None = None
    notes: list[str] = Field(default_factory=list)


class UsageDashboardBucket(BaseModel):
    backend: BackendId
    account_key: str
    account_label: str
    snapshot: SessionRateLimitUsage
    session_ids: list[str] = Field(default_factory=list)


class UsageDashboardResponse(BaseModel):
    buckets: list[UsageDashboardBucket] = Field(default_factory=list)


class SessionRecord(BaseModel):
    id: str
    backend: BackendId
    source: SessionSource
    transport: SessionTransportId = "tmux"
    title: str
    cwd: str
    launch_target_id: str | None = None
    launch_mode: LaunchMode = LaunchMode.AUTO
    repo_name: str | None = None
    branch: str | None = None
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    last_event_at: datetime
    # Per-plugin opaque state, persisted as a JSON column. Each plugin
    # decides what goes in here — Codex / Claude store ``thread_id``;
    # the tmux fallback stores ``tmux_session`` / ``tmux_window`` /
    # ``tmux_pane`` / ``pid``. Generic code (runtime, storage, API)
    # never reads individual keys; only plugins do.
    transport_state: dict[str, Any] = Field(default_factory=dict)
    raw_log_path: str
    structured_log_path: str
    pinned_at: datetime | None = None
    # The session that spawned this one, if any. Set when a session is created
    # by an agent running inside another session (the CLI stamps it from
    # ``WAYPOINT_SESSION_ID``). Used to inherit the spawner's permission mode
    # and to identify subagent sessions. ``None`` for user/top-level sessions.
    spawner_session_id: str | None = None
    worktree_path: str | None = None
    permission_mode: str | None = None
    model: str | None = None
    effort: str | None = None
    args: list[str] = Field(default_factory=list)
    # Backend-specific structured overrides distinct from raw ``args``.
    # Codex uses this for ``--config K=V`` entries; other backends ignore
    # the field. Empty list = none.
    config_overrides: list[str] = Field(default_factory=list)
    context_usage: SessionContextUsage | None = None
    rate_limit_usage: SessionRateLimitUsage | None = None


class EventRecord(BaseModel):
    id: int | None = None
    session_id: str
    ts: datetime
    kind: EventKind
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    sequence: int


class BoardEntry(BaseModel):
    # A single blackboard row. With ``key`` unset the entry is an append-log
    # post (ordered, never overwritten); with ``key`` set it is a key/value
    # cell that the latest post for that ``(channel, key)`` overwrites in place.
    id: int
    channel: str
    # The session that posted this, stamped by the CLI from
    # ``WAYPOINT_SESSION_ID``. ``None`` when posted outside a session (a user
    # via the frontend, an ad-hoc CLI call). Keyed cells authored by a session
    # are pruned on session delete; keyless log posts are durable history and
    # survive the session row being removed.
    author_session_id: str | None = None
    # Snapshot of the authoring session's title at post time. Stays readable
    # after the session row is deleted.
    author_label: str | None = None
    key: str | None = None
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    # Set when the post's text or metadata was edited in place; ``None`` while
    # the post is untouched since it was first written.
    edited_at: datetime | None = None


class BoardChannel(BaseModel):
    channel: str
    entry_count: int
    last_created_at: datetime


class BoardPostRequest(BaseModel):
    text: str
    # When set, upsert the ``(channel, key)`` cell instead of appending.
    key: str | None = None
    author_session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BoardEntryUpdateRequest(BaseModel):
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: datetime


class LaunchTargetSummary(BaseModel):
    id: str
    name: str
    kind: str = "ssh"
    supported_backends: list[BackendId] = Field(default_factory=list)
    default_backend: BackendId = "codex"
    default_cwd: str | None = None


class AssistantSummary(BaseModel):
    session_id: str
    backend: BackendId
    # Backend-native conversation id (e.g. the value for `claude --resume`).
    # Surfaced alongside ``session_id`` so a user can recover the thread
    # outside the app. ``None`` when the backend has no resumable id.
    native_thread_id: str | None = None
    status: SessionStatus
    # Whether the backend can revive this thread after it exits. Drives the
    # assistant UI's choice between offering Reattach and only Clear context.
    supports_reattach: bool = False


class AssistantResetRequest(BaseModel):
    # Rebuild the assistant on a fresh thread. ``backend`` switches the coding
    # agent (``None`` keeps the current one); the rest seed the new thread,
    # where ``None`` means the backend's default.
    backend: BackendId | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None


class AssistantAttachRequest(BaseModel):
    # Adopt an existing backend-native thread as the assistant. ``thread_id`` is
    # the value surfaced by GET /api/backends/{backend}/threads.
    backend: BackendId
    thread_id: str
    launch_target_id: str | None = None


class MeResponse(BaseModel):
    authenticated: bool = True
    default_backend: BackendId = "codex"
    default_cwd: str = "~/"
    launch_targets: list[LaunchTargetSummary] = Field(default_factory=list)
    # Plugin catalogue mirrored from `/api/backends`; lets `/api/me`
    # consumers (the frontend's bootstrap, auth shell) hydrate the
    # backend picker without a second round-trip.
    backends: list[dict[str, Any]] = Field(default_factory=list)
    # The personal-assistant singleton, when enabled. The frontend uses
    # this to locate and render the dedicated assistant page.
    assistant: AssistantSummary | None = None


class EventsPageResponse(BaseModel):
    events: list[EventRecord] = Field(default_factory=list)
    has_more: bool = False


class SessionCreateRequest(BaseModel):
    backend: BackendId
    cwd: str
    launch_target_id: str | None = None
    launch_mode: LaunchMode = LaunchMode.AUTO
    # Pins the transport the agent is driven over. ``None`` keeps the
    # ``launch_mode``-derived behavior; an explicit transport takes
    # precedence over ``launch_mode`` and must be in the chosen agent's
    # ``supported_transports`` (the runtime rejects mismatches with 400).
    transport: SessionTransportId | None = None
    title: str | None = None
    args: list[str] = Field(default_factory=list)
    config_overrides: list[str] = Field(default_factory=list)
    source_mode: SessionSource = SessionSource.MANAGED
    # Set by the CLI from ``WAYPOINT_SESSION_ID`` when an agent inside a session
    # spawns this one. When ``permission_mode`` is unset and the spawner shares
    # this backend, the child inherits the spawner's mode.
    spawner_session_id: str | None = None
    worktree_path: str | None = None
    permission_mode: str | None = None
    model: str | None = None
    effort: str | None = None


class SessionAttachRequest(BaseModel):
    tmux_target: str
    backend_hint: BackendId | None = None
    title: str | None = None


class SessionInputRequest(BaseModel):
    text: str
    submit: bool = True
    command: SessionCommandInvocation | None = None
    items: list[SessionInputItem] | None = None
    # Ids of previously uploaded attachments to deliver alongside the text.
    # Resolved to host paths server-side; see ``AttachmentSpec``.
    attachments: list[str] | None = None


class SessionApprovalRequest(BaseModel):
    decision: str
    text: str | None = None
    approval_id: str | None = None


class SessionPlanApprovalRequest(BaseModel):
    plan_item_id: str
    decision: Literal["accept", "acceptForSession", "decline", "cancel"] = "accept"
    text: str | None = None


class SessionTitleRequest(BaseModel):
    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ]


class SessionPermissionModeRequest(BaseModel):
    mode: str


class SessionModelRequest(BaseModel):
    model: str | None = None


class SessionEffortRequest(BaseModel):
    effort: str | None = None


class BackendModelOption(BaseModel):
    id: str
    label: str
    description: str | None = None
    is_default: bool = False
    hidden: bool = False
    # Reasoning-effort levels this model accepts. Empty list means the model
    # has no effort knob (e.g. Claude Haiku, or Codex models without an
    # entry in `supported_reasoning_efforts`).
    supported_efforts: list[str] = Field(default_factory=list)
    default_effort: str | None = None


class BackendModelListResponse(BaseModel):
    backend: BackendId
    models: list[BackendModelOption] = Field(default_factory=list)
    # The literal model ID to use in requests.
    default_model_id: str | None = None
    # The human-friendly label for the default model.
    default_model_label: str | None = None
    supports_free_text: bool = False
    # Backend-wide default effort (used when no model-specific default
    # applies); falls back to None to mean "let the runtime pick".
    default_effort: str | None = None


class AskQuestionAnswer(BaseModel):
    question: str
    answer: str | None = None
    notes: str | None = None


class SessionAnswerQuestionRequest(BaseModel):
    answer: str
    tool_use_id: str | None = None
    answers: list[AskQuestionAnswer] | None = None


class TerminalSnapshot(BaseModel):
    session_id: str
    text: str
    from_raw_log: bool = True


class ScheduleStatus(StrEnum):
    PENDING = "pending"
    LAUNCHED = "launched"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ScheduledSessionRecord(BaseModel):
    id: str
    backend: BackendId
    cwd: str
    launch_target_id: str | None = None
    launch_mode: LaunchMode = LaunchMode.AUTO
    title: str | None = None
    args: list[str] = Field(default_factory=list)
    config_overrides: list[str] = Field(default_factory=list)
    initial_prompt: str | None = None
    permission_mode: str | None = None
    model: str | None = None
    effort: str | None = None
    scheduled_at: datetime
    created_at: datetime
    status: ScheduleStatus = ScheduleStatus.PENDING
    session_id: str | None = None
    failure_reason: str | None = None


class ScheduleCreateRequest(BaseModel):
    backend: BackendId
    cwd: str
    launch_target_id: str | None = None
    launch_mode: LaunchMode = LaunchMode.AUTO
    title: str | None = None
    args: list[str] = Field(default_factory=list)
    config_overrides: list[str] = Field(default_factory=list)
    initial_prompt: str | None = None
    permission_mode: str | None = None
    model: str | None = None
    effort: str | None = None
    delay_seconds: int | None = None
    scheduled_at: datetime | None = None


class SessionEnvelope(BaseModel):
    type: str
    payload: Mapping[str, Any]
