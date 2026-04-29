from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Backend(StrEnum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"


class SessionSource(StrEnum):
    MANAGED = "managed"
    ATTACHED_TMUX = "attached_tmux"


class SessionTransport(StrEnum):
    TMUX = "tmux"
    CODEX_APP_SERVER = "codex_app_server"
    CLAUDE_CLI = "claude_cli"


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
    backend: Backend
    source: SessionSource
    transport: SessionTransport = SessionTransport.TMUX
    title: str
    cwd: str
    remote_cwd: str | None = None
    launch_target_id: str | None = None
    repo_name: str | None = None
    branch: str | None = None
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    last_event_at: datetime
    tmux_session: str | None = None
    tmux_window: str | None = None
    tmux_pane: str | None = None
    thread_id: str | None = None
    raw_log_path: str
    structured_log_path: str
    pid: int | None = None
    pinned_at: datetime | None = None


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
    supported_backends: list[Backend] = Field(default_factory=list)
    default_backend: Backend = Backend.CODEX
    default_remote_cwd: str | None = None


class MeResponse(BaseModel):
    authenticated: bool = True
    default_backend: Backend = Backend.CODEX
    default_cwd: str = "~/"
    launch_targets: list[LaunchTargetSummary] = Field(default_factory=list)


class SessionCreateRequest(BaseModel):
    backend: Backend
    cwd: str
    remote_cwd: str | None = None
    launch_target_id: str | None = None
    title: str | None = None
    args: list[str] = Field(default_factory=list)
    source_mode: SessionSource = SessionSource.MANAGED


class SessionAttachRequest(BaseModel):
    tmux_target: str
    backend_hint: Backend | None = None
    title: str | None = None


class CodexThreadSummary(BaseModel):
    id: str
    title: str
    cwd: str
    repo_name: str | None = None
    branch: str | None = None
    preview: str | None = None
    created_at: datetime
    updated_at: datetime


class CodexThreadImportRequest(BaseModel):
    thread_id: str
    launch_target_id: str | None = None


class SessionInputRequest(BaseModel):
    text: str
    submit: bool = True


class SessionApprovalRequest(BaseModel):
    decision: str
    text: str | None = None


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
    backend: Backend
    cwd: str
    remote_cwd: str | None = None
    launch_target_id: str | None = None
    title: str | None = None
    args: list[str] = Field(default_factory=list)
    initial_prompt: str | None = None
    scheduled_at: datetime
    created_at: datetime
    status: ScheduleStatus = ScheduleStatus.PENDING
    session_id: str | None = None
    failure_reason: str | None = None


class ScheduleCreateRequest(BaseModel):
    backend: Backend
    cwd: str
    remote_cwd: str | None = None
    launch_target_id: str | None = None
    title: str | None = None
    args: list[str] = Field(default_factory=list)
    initial_prompt: str | None = None
    delay_seconds: int | None = None
    scheduled_at: datetime | None = None


class SessionEnvelope(BaseModel):
    type: str
    payload: Mapping[str, Any]
