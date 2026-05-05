from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field


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


class SessionRecord(BaseModel):
    id: str
    backend: BackendId
    source: SessionSource
    transport: SessionTransportId = "tmux"
    title: str
    cwd: str
    launch_target_id: str | None = None
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
    permission_mode: str | None = None
    model: str | None = None
    effort: str | None = None
    args: list[str] = Field(default_factory=list)
    # Backend-specific structured overrides distinct from raw ``args``.
    # Codex uses this for ``--config K=V`` entries; other backends ignore
    # the field. Empty list = none.
    config_overrides: list[str] = Field(default_factory=list)


class EventRecord(BaseModel):
    id: int | None = None
    session_id: str
    ts: datetime
    kind: EventKind
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    sequence: int


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


class MeResponse(BaseModel):
    authenticated: bool = True
    default_backend: BackendId = "codex"
    default_cwd: str = "~/"
    launch_targets: list[LaunchTargetSummary] = Field(default_factory=list)
    # Plugin catalogue mirrored from `/api/backends`; lets `/api/me`
    # consumers (the frontend's bootstrap, auth shell) hydrate the
    # backend picker without a second round-trip.
    backends: list[dict[str, Any]] = Field(default_factory=list)


class EventsPageResponse(BaseModel):
    events: list[EventRecord] = Field(default_factory=list)
    has_more: bool = False


class SessionCreateRequest(BaseModel):
    backend: BackendId
    cwd: str
    launch_target_id: str | None = None
    title: str | None = None
    args: list[str] = Field(default_factory=list)
    config_overrides: list[str] = Field(default_factory=list)
    source_mode: SessionSource = SessionSource.MANAGED
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


class SessionApprovalRequest(BaseModel):
    decision: str
    text: str | None = None
    approval_id: str | None = None


class SessionTitleRequest(BaseModel):
    title: str


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
