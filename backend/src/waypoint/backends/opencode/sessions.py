"""The live state of one Waypoint session on an OpenCode server.

A Waypoint session covers its OpenCode session and the subagent sessions under
it: their executions and pending asks are its own.
"""

from dataclasses import dataclass, field
from typing import Any

from waypoint.backends.opencode.forms import Form
from waypoint.schemas import SessionContextUsage, SessionStatus


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: Any


@dataclass
class Permission:
    """A pending permission request."""

    id: str
    # The OpenCode session that asked, which the reply goes to.
    session_id: str
    action: str
    # The patterns an approval for the session allows.
    save: tuple[str, ...]
    # The tool call that asked, and the diffs the request proposes.
    call_id: str | None = None
    files: Any = None

    @classmethod
    def parse(cls, payload: Any) -> "Permission | None":
        if not isinstance(payload, dict):
            return None
        request_id, session_id = payload.get("id"), payload.get("sessionID")
        if not isinstance(request_id, str) or not isinstance(session_id, str):
            return None
        source, metadata = payload.get("source"), payload.get("metadata")
        call_id = source.get("id") if isinstance(source, dict) else None
        return cls(
            id=request_id,
            session_id=session_id,
            action=str(payload.get("action") or ""),
            save=tuple(str(item) for item in payload.get("save") or () if item),
            call_id=call_id if isinstance(call_id, str) else None,
            files=metadata.get("files") if isinstance(metadata, dict) else None,
        )


@dataclass
class SessionState:
    session_id: str
    cwd: str
    # The OpenCode session this Waypoint session drives.
    root_id: str
    # Subagent sessions under the root, at any depth.
    children: set[str] = field(default_factory=set)
    # The sessions of the tree with a running execution.
    running: set[str] = field(default_factory=set)
    # Whether the human interrupted the session's last turn.
    interrupted: bool = False
    # Set when the interrupt stopped subagents: OpenCode then runs the parent to
    # take their results, which is still the interrupted turn.
    stopped_subagents: bool = False
    permissions: dict[str, Permission] = field(default_factory=dict)
    forms: dict[str, Form] = field(default_factory=dict)
    # Asks with a reply in flight, whose resolution is Waypoint's own.
    replying: set[str] = field(default_factory=set)
    # Whether ``forms`` is authoritative. A live session sees every form; a
    # restored one only after listing the server's, until when an unknown
    # question may still be live.
    forms_seeded: bool = True
    forms_seed_attempted_at: float = 0.0
    # Tool calls still running, by call id.
    tools: dict[str, ToolCall] = field(default_factory=dict)
    # The diffs a call's permission request showed, by call id.
    proposed_files: dict[str, Any] = field(default_factory=dict)
    # Text parts whose deltas have arrived.
    streamed: set[str] = field(default_factory=set)
    # The model of each assistant message, by message id.
    message_models: dict[str, str] = field(default_factory=dict)
    # The root session's model and agent as the server holds them.
    model: str | None = None
    effort: str | None = None
    agent: str | None = None
    # The permission mode leaving plan mode returns to.
    pre_plan_mode: str | None = None
    # Whether the running turn's steps changed files.
    changed_files: bool = False
    context_usage: SessionContextUsage | None = None
    closing: bool = False
    # Orders events against listings in flight: see ``mark``.
    _clock: int = 0
    _touched: dict[str, int] = field(default_factory=dict)

    def owns(self, opencode_session_id: str) -> bool:
        return (
            opencode_session_id == self.root_id or opencode_session_id in self.children
        )

    def mark(self) -> int:
        """A point in time: take one before listing the server's state, so that
        what the stream reports afterwards wins over the listing."""
        self._clock += 1
        return self._clock

    def touch(self, key: str) -> None:
        """Note that the stream reported on a session or ask."""
        self._clock += 1
        self._touched[key] = self._clock

    def touched_since(self, key: str, mark: int) -> bool:
        return self._touched.get(key, 0) > mark

    def status(self) -> SessionStatus:
        if self.permissions or self.forms:
            return SessionStatus.WAITING_INPUT
        if self.running:
            return SessionStatus.RUNNING
        if self.interrupted:
            return SessionStatus.INTERRUPTED
        return SessionStatus.IDLE

    def execution_started(self, opencode_session_id: str) -> None:
        self.touch(opencode_session_id)
        self.running.add(opencode_session_id)
        if opencode_session_id == self.root_id:
            if not self.stopped_subagents:
                self.interrupted = False
            self.changed_files = False

    def execution_ended(
        self, opencode_session_id: str, *, by_human: bool = False
    ) -> None:
        self.touch(opencode_session_id)
        self.running.discard(opencode_session_id)
        if opencode_session_id == self.root_id and by_human:
            self.interrupted = True

    def interrupt_requested(self) -> None:
        if self.running or self.permissions or self.forms:
            self.interrupted = True
        if self.running - {self.root_id}:
            self.stopped_subagents = True

    def input_sent(self) -> None:
        """New input from the human ends what their interrupt held."""
        self.stopped_subagents = False
        self.interrupted = False

    def add_permission(self, permission: Permission) -> None:
        self.touch(permission.id)
        self.permissions[permission.id] = permission
        if permission.call_id and permission.files:
            self.proposed_files[permission.call_id] = permission.files

    def add_form(self, form: Form) -> None:
        self.touch(form.id)
        self.forms[form.id] = form

    def forget_ask(self, ask_id: str) -> None:
        self.touch(ask_id)
        self.permissions.pop(ask_id, None)
        self.forms.pop(ask_id, None)

    def seed_running(self, active: set[str], mark: int) -> None:
        """Adopt which sessions of the tree the server lists as running."""
        for session_id in {self.root_id, *self.children}:
            if self.touched_since(session_id, mark):
                continue
            if session_id in active:
                self.running.add(session_id)
            else:
                self.running.discard(session_id)
