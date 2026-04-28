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


class MeResponse(BaseModel):
    authenticated: bool = True
    remote_codex_enabled: bool = False
    default_remote_cwd: str | None = None


class SessionCreateRequest(BaseModel):
    backend: Backend
    cwd: str
    remote_cwd: str | None = None
    title: str | None = None
    args: list[str] = Field(default_factory=list)
    source_mode: SessionSource = SessionSource.MANAGED


class SessionAttachRequest(BaseModel):
    tmux_target: str
    backend_hint: Backend | None = None
    title: str | None = None


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


class SessionEnvelope(BaseModel):
    type: str
    payload: Mapping[str, Any]
