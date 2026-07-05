from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from waypoint.attachments import ResolvedAttachment, append_attachment_paths
from waypoint.backends.approvals import is_approve_decision
from waypoint.backends.claude_code.models import (
    claude_context_window_for_model,
    claude_model_family,
    normalize_claude_model_id,
)
from waypoint.backends.claude_code.normalize import (
    TASK_TOOL_NAMES,
    TaskListTracker,
    extract_created_task_id,
    format_approval_text,
    format_compact_boundary,
    format_rate_limit,
    format_status_event,
    format_task_snapshot,
    iter_content_blocks,
    stringify_tool_result,
)

# Re-exported from the backend plugin so legacy imports keep resolving;
# the source of truth lives in `backends/claude_code/permission_modes.py`.
from waypoint.backends.claude_code.permission_modes import (
    CLAUDE_ACCEPT_EDITS_TOOLS,
    CLAUDE_AUTO_APPROVE_MODES,
    CLAUDE_PERMISSION_MODES,
)
from waypoint.backends.diff_preview import (
    ChangeType,
    build_preview,
    file_from_old_new,
    unavailable_file,
)
from waypoint.schemas import (
    EventKind,
    SessionContextUsage,
    SessionRateLimitUsage,
    SessionStatus,
)

log = logging.getLogger("waypoint.claude_cli")


def _user_content(
    text: str, attachments: list[ResolvedAttachment] | None
) -> str | list[dict[str, Any]]:
    """Build the ``message.content`` for a user turn.

    Returns the plain string when there are no attachments (preserving the
    historical envelope shape). Otherwise returns Anthropic content blocks:
    images embed inline as base64 ``image`` blocks; non-image files degrade
    to their host paths appended to the text block, which Claude reads via
    its file tools.
    """
    if not attachments:
        return text
    images = [item for item in attachments if item.is_image]
    files = [item for item in attachments if not item.is_image]
    body = append_attachment_paths(text, files)
    blocks: list[dict[str, Any]] = []
    if body:
        blocks.append({"type": "text", "text": body})
    for image in images:
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.spec.mime,
                    "data": image.read_base64(),
                },
            }
        )
    return blocks or text


CONTROL_REQUEST_TIMEOUT_SECONDS = 10.0
# Claude's stream-json output can emit a single line larger than asyncio's
# default 64 KB StreamReader buffer (e.g. tool results carrying large file
# contents). Give the reader plenty of room so one fat line doesn't tear down
# the session.
CLAUDE_STREAM_BUFFER_LIMIT = 16 * 1024 * 1024


async def _drain_until_newline(reader: asyncio.StreamReader) -> None:
    """Read and discard bytes until a newline (or EOF) so the reader can
    resume on the next line after a buffer-overrun event."""
    while True:
        try:
            chunk = await reader.readuntil(b"\n")
        except asyncio.LimitOverrunError as exc:
            await reader.readexactly(exc.consumed)
            continue
        except asyncio.IncompleteReadError:
            return
        if chunk.endswith(b"\n"):
            return


async def _read_stream_line(
    reader: asyncio.StreamReader, session_id: str, stream: str
) -> bytes | None:
    """Read one newline-terminated line, surviving an over-limit line.

    Returns the line (with trailing newline), ``b""`` at EOF, or ``None`` when an
    oversized line was drained and skipped so the caller can continue. We read
    via ``readuntil`` rather than ``readline`` because ``readline`` masks
    ``LimitOverrunError`` as a plain ``ValueError`` that loses ``consumed`` and
    can't be drained — so a single giant stream-json line (e.g. a base64 tool
    result echoing a large attachment) would otherwise tear down the reader."""
    try:
        return await reader.readuntil(b"\n")
    except asyncio.LimitOverrunError as exc:
        await reader.readexactly(exc.consumed)
        await _drain_until_newline(reader)
        log.warning(
            "claude %s line exceeded buffer limit; dropped",
            stream,
            extra={"session_id": session_id, "consumed_bytes": exc.consumed},
        )
        return None
    except asyncio.IncompleteReadError as exc:
        # EOF before a newline: hand back the trailing partial (empty once the
        # stream is fully drained, which the caller treats as EOF).
        return exc.partial


def _auto_approve_for_mode(
    mode: str,
    tool_name: object,
    tool_input: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    if mode in CLAUDE_AUTO_APPROVE_MODES:
        return {
            "permissionDecision": "allow",
            "permissionDecisionReason": f"auto-approved by mode={mode}",
        }
    if mode == "acceptEdits" and tool_name in CLAUDE_ACCEPT_EDITS_TOOLS:
        return {
            "permissionDecision": "allow",
            "permissionDecisionReason": "auto-approved by mode=acceptEdits",
        }
    if (
        mode == "plan"
        and tool_name in ("Write", "Edit", "MultiEdit")
        and tool_input is not None
    ):
        path = str(tool_input.get("file_path") or "")
        if _is_plan_file_path(path):
            return {
                "permissionDecision": "allow",
                "permissionDecisionReason": (
                    "Plan-file write auto-approved by plan mode"
                ),
            }
    return None


# Claude in plan mode writes its plan to ~/.claude/plans/<slug>.md before
# calling ExitPlanMode. That write is a meta-operation the binary itself
# does — surfacing an approval card for it duplicates the ExitPlanMode card
# the user already sees. Detect the canonical location (with /private/var
# realpath quirks on macOS) so we can pass it through silently.
def _apply_plan_edit(content: str, tool_name: str, tool_input: dict[str, Any]) -> str:
    """Return `content` with the Edit/MultiEdit patch applied in-memory.

    Used to keep `last_plan_content` current without reading the file from
    disk — which would fail for remote Claude Code sessions where the plan
    lives on the SSH target, not the Waypoint host.
    """
    if tool_name == "Edit":
        old = str(tool_input.get("old_string") or "")
        new = str(tool_input.get("new_string") or "")
        count = None if bool(tool_input.get("replace_all", False)) else 1
        return (
            content.replace(old, new)
            if count is None
            else content.replace(old, new, count)
        )
    if tool_name == "MultiEdit":
        for edit in tool_input.get("edits") or []:
            if not isinstance(edit, dict):
                continue
            old = str(edit.get("old_string") or "")
            new = str(edit.get("new_string") or "")
            content = content.replace(old, new, 1)
    return content


def _apply_edit(content: str, edit: dict[str, Any]) -> str:
    """Apply a single Edit/MultiEdit patch, raising ``ValueError`` on mismatch.

    Used to render a full-context diff preview from the current file content.
    """
    old = edit.get("old_string")
    new = edit.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str):
        raise ValueError("edit payload did not include old_string/new_string")
    if old == "":
        raise ValueError("edit payload old_string was empty")
    if old not in content:
        raise ValueError("old_string was not found in the current file")
    count = -1 if bool(edit.get("replace_all", False)) else 1
    return content.replace(old, new, count)


def _is_plan_file_path(path: str) -> bool:
    if not path:
        return False
    return "/.claude/plans/" in path


# Maps a Waypoint per-session mode to the value the Claude CLI accepts on
# `--permission-mode`. `auto` and `dontAsk` are Waypoint-side short-circuits
# with no Claude equivalent — we launch Claude in `default` and auto-allow
# gated `can_use_tool` requests in `_auto_approve_for_mode`.
def claude_cli_mode_for(mode: str) -> str:
    if mode in {"auto", "dontAsk"}:
        return "default"
    if mode in CLAUDE_PERMISSION_MODES:
        return mode
    return "default"


EmitEvent = Callable[
    [str, EventKind, str, dict[str, Any], SessionStatus],
    Coroutine[Any, Any, None],
]
InitCallback = Callable[[str, dict[str, Any]], None]
SessionUpdateCallback = Callable[[str, dict[str, Any], bool], Awaitable[Any]]
LaunchFactory = Callable[
    [
        str,
        str,
        str,
        bool,
        str,
        str | None,
        str | None,
        list[str],
        str | None,
        dict[str, str],
    ],
    "ClaudeLaunchSpec",
]

# Which tools require approval is decided by the binary's own permission
# policy: with `--permission-prompt-tool stdio` it emits a `can_use_tool`
# control_request for every tool that would otherwise prompt (Bash, Edit,
# Write, …, plus ExitPlanMode, AskUserQuestion, and Workflow). Read/Grep/Glob
# and friends are auto-allowed and never reach us.


STDERR_TAIL_LINES = 50


@dataclass
class ClaudePendingApproval:
    tool_use_id: str
    payload: dict[str, Any]
    # ``request_id`` of the binary's ``can_use_tool`` control_request; the
    # decision is delivered by writing a ``control_response`` carrying it back
    # over stdin (there is no future to resolve — the stdout reader must never
    # block waiting on a user decision).
    request_id: str


@dataclass
class ClaudeLaunchSpec:
    args: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None


@dataclass
class ClaudeSessionState:
    session_id: str
    cwd: str
    process: asyncio.subprocess.Process
    claude_session_id: str
    stdout_task: asyncio.Task[None]
    stderr_task: asyncio.Task[None]
    wait_task: asyncio.Task[None]
    pending: dict[str, ClaudePendingApproval] = field(default_factory=dict)
    emitted_diff_preview_tool_ids: set[str] = field(default_factory=set)
    # tool_use_ids whose tool_call we deliberately suppressed (ExitPlanMode):
    # the binary still echoes a tool_result for them — our injected
    # approve/decline verdict — which would otherwise render as an orphan
    # 0-call tool run. Suppress that result's display too.
    suppressed_result_tool_use_ids: set[str] = field(default_factory=set)
    file_edit_preview_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Folds Claude Code's incremental Task tool stream (CC >= v2.1.142) back
    # into a single evolving todo card. `pending_task_creates` holds each
    # TaskCreate's input until its tool_result reveals the assigned task id;
    # `task_card_item_id` is the stable item_id the snapshots merge under, and
    # is rotated when a new task group starts so completed groups keep their
    # own card.
    task_tracker: TaskListTracker = field(default_factory=TaskListTracker)
    pending_task_creates: dict[str, dict[str, Any]] = field(default_factory=dict)
    task_card_item_id: str | None = None
    last_message_text: dict[str, str] = field(default_factory=dict)
    stderr_tail: deque[str] = field(
        default_factory=lambda: deque(maxlen=STDERR_TAIL_LINES)
    )
    pending_controls: dict[str, asyncio.Future[dict[str, Any]]] = field(
        default_factory=dict
    )
    permission_mode: str = "default"
    # Mode the session was in immediately before transitioning into "plan".
    # ExitPlanMode approval restores this so users who were in (e.g.)
    # acceptEdits before opening a plan don't get bumped down to default.
    pre_plan_mode: str | None = None
    last_plan_path: str | None = None
    last_plan_content: str | None = None
    closing: bool = False
    model: str | None = None
    # The raw concrete model id the CLI last reported resolving to (e.g.
    # ``claude-sonnet-5``), mirrored from the persisted
    # ``SessionRecord.resolved_model`` so repeated ``system.init`` events for
    # the same model don't trigger redundant storage writes. Unlike ``model``
    # (normalized to a family, e.g. ``sonnet``), this is never normalized.
    resolved_model: str | None = None
    context_usage_snapshot: SessionContextUsage | None = None
    context_usage_signature: tuple[int, int | None] | None = None
    rate_limit_usage_snapshot: SessionRateLimitUsage | None = None
    rate_limit_usage_signature: str | None = None
    rate_limit_probe: Callable[[], Awaitable[SessionRateLimitUsage | None]] | None = (
        None
    )
    rate_limit_refresh_task: asyncio.Task[None] | None = None
    # Reasoning effort. Claude's CLI accepts `--effort <level>` at launch
    # only — there is no in-process control_request to swap it — so changing
    # this value at runtime requires terminating and respawning the process
    # with `--resume <claude_session_id>` and the new flag.
    effort: str | None = None
    # Extra CLI flags appended verbatim after all Waypoint-managed flags.
    # Carried across set_effort respawns so user-supplied args survive
    # mid-session effort changes.
    custom_args: list[str] = field(default_factory=list)
    # Effective user-editable launch environment, carried across respawns.
    launch_env: dict[str, str] = field(default_factory=dict)
    # Captures the launch_factory used to spawn this session so set_effort
    # can respawn through the same factory (local vs. remote SSH) without
    # re-resolving target config.
    launch_factory: LaunchFactory | None = None
    slash_commands: tuple[str, ...] = ()


class ClaudeCliError(RuntimeError):
    pass


class ClaudeCliAdapter:
    def __init__(
        self,
        emit_event: EmitEvent,
        binary: str | None = None,
        launch_factory: LaunchFactory | None = None,
        on_init: InitCallback | None = None,
        on_session_update: SessionUpdateCallback | None = None,
        default_model_id: str | None = None,
    ) -> None:
        self._emit_event = emit_event
        self._binary = binary
        self._launch_factory = launch_factory
        self._on_init = on_init
        self._on_session_update = on_session_update
        self._default_model_id = normalize_claude_model_id(default_model_id)
        self._sessions: dict[str, ClaudeSessionState] = {}
        # Folded todo state stashed by terminate_session and consumed by the
        # next resume-spawn, so a respawn (set_effort, reattach) restores the
        # tracker instead of stubbing the CLI's TaskUpdate deltas for tasks
        # created before the respawn. Keyed by session id.
        self._carried_task_state: dict[str, tuple[TaskListTracker, str | None]] = {}
        self._approval_lock = asyncio.Lock()

    async def start_session(
        self,
        session_id: str,
        cwd: str,
        claude_session_id: str,
        launch_factory_override: LaunchFactory | None = None,
        permission_mode: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        custom_args: list[str] | None = None,
        fork_from_claude_session_id: str | None = None,
        launch_env: dict[str, str] | None = None,
    ) -> str:
        state = await self._spawn(
            session_id,
            cwd,
            claude_session_id,
            resume=False,
            launch_factory_override=launch_factory_override,
            permission_mode=permission_mode,
            model=model,
            effort=effort,
            custom_args=custom_args or [],
            fork_from_claude_session_id=fork_from_claude_session_id,
            launch_env=launch_env or {},
        )
        return state.claude_session_id

    async def restore_session(
        self,
        session_id: str,
        cwd: str,
        claude_session_id: str,
        launch_factory_override: LaunchFactory | None = None,
        permission_mode: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        custom_args: list[str] | None = None,
        launch_env: dict[str, str] | None = None,
    ) -> None:
        await self._spawn(
            session_id,
            cwd,
            claude_session_id,
            resume=True,
            launch_factory_override=launch_factory_override,
            permission_mode=permission_mode,
            model=model,
            effort=effort,
            custom_args=custom_args or [],
            launch_env=launch_env or {},
        )

    async def set_permission_mode(self, session_id: str, mode: str) -> None:
        """Send a control_request set_permission_mode envelope to the CLI.

        Wire format documented in tmp/docs/BACKEND_CONTROL_PROTOCOLS.md.
        """
        if mode not in CLAUDE_PERMISSION_MODES:
            raise ClaudeCliError(f"unsupported permission mode: {mode}")
        request_id = f"set-mode-{uuid.uuid4()}"
        await self._send_control_request(
            session_id,
            request_id,
            {"subtype": "set_permission_mode", "mode": mode},
        )
        # Mirror the new mode locally so _handle_can_use_tool can auto-approve
        # without consulting the database on every request. When transitioning
        # into plan from any other mode, also record the outgoing mode so
        # ExitPlanMode approval can restore it instead of always dropping to
        # default.
        state = self._sessions.get(session_id)
        if state is not None:
            if mode == "plan" and state.permission_mode != "plan":
                state.pre_plan_mode = state.permission_mode
            state.permission_mode = mode

    async def set_model(self, session_id: str, model: str | None) -> None:
        """Send a control_request set_model envelope to the CLI.

        Wire format documented in tmp/docs/BACKEND_CONTROL_PROTOCOLS.md. Pass
        ``model=None`` to revert to the session default. The CLI accepts both
        shortened aliases (``opus``, ``sonnet``, ``haiku``) and full first-party
        IDs (``claude-opus-4-8``); append ``[1m]`` for 1M-context variants.
        """
        request_id = f"set-model-{uuid.uuid4()}"
        payload: dict[str, Any] = {"subtype": "set_model"}
        if model:
            payload["model"] = model
        await self._send_control_request(session_id, request_id, payload)
        state = self._sessions.get(session_id)
        if state is not None:
            previous_model = state.model
            state.model = self._effective_model_id(model)
            if state.model != previous_model:
                await self._refresh_context_usage(state)

    def session_model(self, session_id: str) -> str | None:
        state = self._sessions.get(session_id)
        return state.model if state is not None else None

    async def register_rate_limit_probe(
        self,
        session_id: str,
        probe: Callable[[], Awaitable[SessionRateLimitUsage | None]],
        *,
        refresh_interval_seconds: float = 60.0,
    ) -> None:
        state = self._require_session(session_id)
        state.rate_limit_probe = probe
        if state.rate_limit_refresh_task is not None:
            state.rate_limit_refresh_task.cancel()
        state.rate_limit_refresh_task = asyncio.create_task(
            self._refresh_rate_limit_usage_loop(
                state, refresh_interval_seconds=refresh_interval_seconds
            )
        )

    async def force_refresh_rate_limit_usage(self, session_id: str) -> None:
        # User-driven path: run the registered probe inline so the caller's
        # response carries the fresh snapshot instead of racing the WS push.
        state = self._sessions.get(session_id)
        if state is None:
            return
        await self._refresh_rate_limit_usage(state)

    def session_slash_commands(self, session_id: str) -> tuple[str, ...]:
        state = self._sessions.get(session_id)
        return state.slash_commands if state is not None else ()

    async def set_effort(self, session_id: str, effort: str | None) -> None:
        """Swap the session's reasoning-effort by relaunching the binary.

        Claude exposes effort only via the ``--effort`` launch flag and the
        ``/effort`` slash command. The slash command is blocklisted in
        ``--print`` mode (the binary's `Tu()` set), and there is no
        ``set_effort`` control_request, so the only way to change it from
        Waypoint is to terminate the running process and respawn it with
        ``--resume <claude_session_id>`` plus the new ``--effort``. Conversation
        history is preserved by ``--resume``; in-flight tool approvals are
        denied by ``terminate_session`` before the respawn.
        """
        state = self._require_session(session_id)
        previous = state
        cwd = previous.cwd
        claude_session_id = previous.claude_session_id
        permission_mode = previous.permission_mode
        model = previous.model
        custom_args = previous.custom_args
        launch_factory = previous.launch_factory
        launch_env = dict(previous.launch_env)
        await self.terminate_session(session_id)
        await self._spawn(
            session_id,
            cwd,
            claude_session_id,
            resume=True,
            launch_factory_override=launch_factory,
            permission_mode=permission_mode,
            model=model,
            effort=effort or None,
            custom_args=custom_args,
            launch_env=launch_env,
        )

    def session_effort(self, session_id: str) -> str | None:
        state = self._sessions.get(session_id)
        return state.effort if state is not None else None

    async def _send_control_request(
        self, session_id: str, request_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        state = self._require_session(session_id)
        if state.process.returncode is not None:
            raise ClaudeCliError(
                self._format_dead_process_error(state, state.process.returncode)
            )
        if state.process.stdin is None or state.process.stdin.is_closing():
            raise ClaudeCliError(
                self._format_dead_process_error(state, state.process.returncode)
            )
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_event_loop().create_future()
        )
        state.pending_controls[request_id] = future
        envelope = {
            "type": "control_request",
            "request_id": request_id,
            "request": request,
        }
        line = (json.dumps(envelope) + "\n").encode("utf-8")
        state.process.stdin.write(line)
        try:
            await state.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            state.pending_controls.pop(request_id, None)
            future.cancel()
            raise ClaudeCliError(f"claude stdin write failed: {exc}") from exc
        try:
            response = await asyncio.wait_for(
                future, timeout=CONTROL_REQUEST_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            state.pending_controls.pop(request_id, None)
            raise ClaudeCliError(
                f"claude control_request timed out: {request.get('subtype')}"
            ) from exc
        if response.get("subtype") == "error":
            raise ClaudeCliError(
                response.get("error") or "claude control_request rejected"
            )
        return response

    def _handle_control_response(
        self, state: ClaudeSessionState, event: dict[str, Any]
    ) -> None:
        response = event.get("response") or {}
        request_id = response.get("request_id")
        if not isinstance(request_id, str):
            return
        future = state.pending_controls.pop(request_id, None)
        if future is None or future.done():
            return
        future.set_result(response)

    async def send_input(
        self,
        session_id: str,
        text: str,
        attachments: list[ResolvedAttachment] | None = None,
    ) -> None:
        state = self._require_session(session_id)
        if state.process.returncode is not None:
            raise ClaudeCliError(
                self._format_dead_process_error(state, state.process.returncode)
            )
        if state.process.stdin is None or state.process.stdin.is_closing():
            raise ClaudeCliError(
                self._format_dead_process_error(state, state.process.returncode)
            )
        envelope = {
            "type": "user",
            "message": {"role": "user", "content": _user_content(text, attachments)},
        }
        line = (json.dumps(envelope) + "\n").encode("utf-8")
        state.process.stdin.write(line)
        try:
            await state.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ClaudeCliError(f"claude stdin write failed: {exc}") from exc

    async def interrupt(self, session_id: str) -> None:
        state = self._require_session(session_id)
        if state.process.returncode is not None:
            return
        # The stream-json control_request `interrupt` cancels the in-flight
        # turn while keeping the binary alive, mirroring the TUI Ctrl+C
        # behaviour. Fall back to SIGINT only if the request fails — that
        # ends the process (rc=0 in `-p` mode), so the session would have to
        # be resumed.
        request_id = f"interrupt-{uuid.uuid4()}"
        try:
            await self._send_control_request(
                session_id, request_id, {"subtype": "interrupt"}
            )
            return
        except (ClaudeCliError, TimeoutError) as exc:
            log.warning(
                "claude interrupt control_request failed; falling back to SIGINT: %s",
                exc,
                extra={"session_id": session_id},
            )
        with suppress(ProcessLookupError):
            state.process.send_signal(2)  # SIGINT

    async def respond_to_ask_question(
        self,
        session_id: str,
        answer_text: str,
        tool_use_id: str | None = None,
    ) -> bool:
        """Answer an AskUserQuestion parked on a ``can_use_tool`` request.

        AskUserQuestion arrives over the same ``can_use_tool`` channel as
        approvals so the binary doesn't auto-decline. Once the user answers,
        we deny the tool and carry the answer payload as the message — that
        string becomes the tool_result Claude reads, matching the binary's
        own `User has answered your questions: …` shape.
        """
        state = self._sessions.get(session_id)
        if state is None or not state.pending:
            return False
        pending: ClaudePendingApproval | None = None
        if tool_use_id and tool_use_id in state.pending:
            candidate = state.pending[tool_use_id]
            if candidate.payload.get("tool_name") == "AskUserQuestion":
                pending = candidate
        if pending is None:
            for tid, candidate in state.pending.items():
                if candidate.payload.get("tool_name") == "AskUserQuestion":
                    tool_use_id = tid
                    pending = candidate
                    break
        if pending is None or tool_use_id is None:
            return False
        # Deny the tool and carry the answer in the message — the binary reads
        # that string as the tool_result, matching its own
        # "User has answered your questions: …" shape.
        await self._write_permission_response(
            state,
            pending.request_id,
            "deny",
            (
                pending.payload.get("tool_input")
                if isinstance(pending.payload.get("tool_input"), dict)
                else None
            ),
            (
                f"User has answered your questions: {answer_text}. "
                "You can now continue with the user's answers in mind."
            ),
        )
        state.pending.pop(tool_use_id, None)
        return True

    def has_pending_ask_question(self, session_id: str) -> bool:
        state = self._sessions.get(session_id)
        if state is None:
            return False
        return any(
            entry.payload.get("tool_name") == "AskUserQuestion"
            for entry in state.pending.values()
        )

    async def respond_to_approval(
        self,
        session_id: str,
        decision: str,
        text: str | None = None,
        approval_id: str | None = None,
    ) -> bool:
        state = self._sessions.get(session_id)
        if state is None or not state.pending:
            return False

        pending: ClaudePendingApproval | None
        if approval_id:
            if approval_id not in state.pending:
                return False
            pending = state.pending[approval_id]
        else:
            # Resolve oldest pending first if no ID is specified
            approval_id, pending = next(iter(state.pending.items()))

        mapped = self._map_decision(decision)
        tool_name = pending.payload.get("tool_name")
        tool_input = pending.payload.get("tool_input")
        tool_input_dict = tool_input if isinstance(tool_input, dict) else None
        # ExitPlanMode is special: in `-p` mode the binary's tool echoes the
        # dialog title ("Exit plan mode?") as the result, which Claude reads as
        # "dismissed" even when the user accepted. Deny the tool and carry the
        # verdict in the message so Claude proceeds correctly. On accept we
        # also flip the binary out of plan mode via control_request, mirroring
        # what the TUI does after the dialog returns.
        if tool_name == "ExitPlanMode":
            response = await self._exit_plan_mode_response(
                state, mapped, pending.payload, text
            )
            await self._write_permission_response(
                state,
                pending.request_id,
                response["permissionDecision"],
                tool_input_dict,
                response["permissionDecisionReason"],
            )
        elif mapped == "allow":
            await self._write_permission_response(
                state,
                pending.request_id,
                "allow",
                tool_input_dict,
                permission_updates=self._permission_updates_for(
                    decision, pending.payload
                ),
            )
        else:
            reason = "denied by user"
            if text:
                reason = f"{reason}\n\nUser note:\n{text}"
            await self._write_permission_response(
                state, pending.request_id, "deny", tool_input_dict, reason
            )
        state.pending.pop(approval_id, None)
        return True

    def _permission_updates_for(
        self, decision: str, payload: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """Build ``permission_updates`` for a "for-session"/"always" allow.

        Relays the binary's own ``permission_suggestions`` verbatim. Currently
        dormant: ``claude_code`` advertises only one-shot approve/decline
        because the binary ignores these ``permission_updates`` in ``-p`` mode
        (see the ``approval_decisions`` note in plugin.py). Kept as scaffolding
        for when in-session suppression is implemented adapter-side.
        """
        lowered = decision.strip().lower()
        if lowered not in ("acceptforsession", "acceptalways"):
            return None
        suggestions = payload.get("permission_suggestions")
        if not isinstance(suggestions, list):
            return None
        updates = [s for s in suggestions if isinstance(s, dict)]
        return updates or None

    async def _exit_plan_mode_response(
        self,
        state: ClaudeSessionState,
        mapped: str,
        payload: dict[str, Any],
        note: str | None = None,
    ) -> dict[str, str]:
        # Phrasing mirrors the Claude binary's own ExitPlanMode tool_result so
        # the model sees the same approval/decline shape it was tuned for —
        # including the saved-plan path and the approved plan body, since
        # plan mode was tuned around that exact context.
        if mapped == "allow":
            target_mode = state.pre_plan_mode or "default"
            if target_mode == "plan":
                target_mode = "default"
            state.pre_plan_mode = None
            try:
                await self.set_permission_mode(state.session_id, target_mode)
            except (ClaudeCliError, TimeoutError) as exc:
                log.warning(
                    "claude set_permission_mode after plan approval failed: %s",
                    exc,
                    extra={"session_id": state.session_id},
                )
            tool_input = payload.get("tool_input")
            plan = ""
            if isinstance(tool_input, dict):
                plan = str(tool_input.get("plan") or "")
            lines = [
                "User has approved your plan. You can now start coding. "
                "Start with updating your todo list if applicable.",
            ]
            if state.last_plan_path:
                lines.append(f"Your plan has been saved to: {state.last_plan_path}")
                lines.append(
                    "You can refer back to it if needed during implementation."
                )
            if plan.strip():
                lines.append("## Approved Plan:")
                lines.append(plan)
            if note:
                lines.append("\nUser note:")
                lines.append(note)
            state.last_plan_path = None
            state.last_plan_content = None
            return {
                "permissionDecision": "deny",
                "permissionDecisionReason": "\n".join(lines),
            }

        reason = (
            "User declined your plan. Revise the plan based on any "
            "feedback or wait for further direction."
        )
        if note:
            reason = f"{reason}\n\nUser note:\n{note}"

        return {
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }

    async def _handle_can_use_tool(
        self, state: ClaudeSessionState, event: dict[str, Any]
    ) -> None:
        """Handle a ``can_use_tool`` control_request from the binary.

        This is the single tool-approval entry point (it replaces the former
        PreToolUse HTTP hook). It must never block the stdout reader: it
        either writes an immediate ``control_response`` (auto-approve by
        mode) or registers a pending approval and returns, leaving the
        decision to ``respond_to_approval`` / ``respond_to_ask_question``.
        """
        request = event.get("request") or {}
        request_id = event.get("request_id")
        tool_name = request.get("tool_name")
        tool_use_id = str(request.get("tool_use_id") or "")
        raw_input = request.get("input")
        tool_input_dict = raw_input if isinstance(raw_input, dict) else None
        if not isinstance(request_id, str) or not tool_use_id:
            await self._write_permission_response(
                state, request_id, "deny", tool_input_dict, "missing identifiers"
            )
            return
        # Normalized payload reused by the diff/emit/approval-text helpers,
        # mirroring the shape the former hook produced.
        payload: dict[str, Any] = {
            "waypoint_session_id": state.session_id,
            "tool_name": tool_name,
            "tool_input": tool_input_dict if tool_input_dict is not None else raw_input,
            "tool_use_id": tool_use_id,
            "permission_suggestions": request.get("permission_suggestions"),
        }
        # The binary's can_use_tool input carries only the raw tool args, so
        # we build the diff preview here (full-context locally; synthesized
        # from old_string/new_string for remote sessions whose files we
        # can't read).
        diff_preview = self._diff_preview_from_input(
            tool_name, tool_input_dict, state.cwd
        )
        if diff_preview is not None:
            payload["diff_preview"] = diff_preview
            state.file_edit_preview_metadata[tool_use_id] = {
                "tool_name": tool_name,
                "tool_input": payload["tool_input"],
                "diff_preview": diff_preview,
            }
        async with self._approval_lock:
            # AskUserQuestion always surfaces to the user — auto-approving any
            # mode would let the binary's defer path decline before the answer
            # arrives.
            if tool_name == "AskUserQuestion":
                auto = None
            else:
                auto = _auto_approve_for_mode(
                    state.permission_mode, tool_name, tool_input_dict
                )
            if auto is not None:
                # Stash the plan-file path so ExitPlanMode can echo it back
                # to Claude in the same shape the binary's TUI uses.
                if (
                    state.permission_mode == "plan"
                    and tool_name in ("Write", "Edit", "MultiEdit")
                    and tool_input_dict is not None
                ):
                    path = str(tool_input_dict.get("file_path") or "")
                    if _is_plan_file_path(path):
                        state.last_plan_path = path
                        if tool_name == "Write":
                            content = tool_input_dict.get("content")
                            if isinstance(content, str):
                                state.last_plan_content = content
                        elif isinstance(state.last_plan_content, str):
                            state.last_plan_content = _apply_plan_edit(
                                state.last_plan_content, tool_name, tool_input_dict
                            )
                await self._emit_tool_diff_preview(state, payload)
                await self._write_permission_response(
                    state,
                    request_id,
                    auto["permissionDecision"],
                    tool_input_dict,
                    auto["permissionDecisionReason"],
                )
                return

            # Inject the saved plan text into ExitPlanMode so the frontend can
            # render it inside the approval card.
            if tool_name == "ExitPlanMode" and state.last_plan_path:
                if tool_input_dict is None:
                    tool_input_dict = {}
                    payload["tool_input"] = tool_input_dict
                if state.last_plan_content is not None:
                    tool_input_dict["plan"] = state.last_plan_content
                else:
                    try:
                        tool_input_dict["plan"] = Path(state.last_plan_path).read_text(
                            encoding="utf-8"
                        )
                    except Exception as exc:
                        log.warning(
                            "failed to read plan file for ExitPlanMode approval card",
                            extra={"path": state.last_plan_path, "error": str(exc)},
                        )

            if tool_use_id in state.pending:
                # Binary re-sent the same tool call; refresh the request_id so
                # the eventual response targets the live control_request.
                state.pending[tool_use_id].request_id = request_id
                return
            state.pending[tool_use_id] = ClaudePendingApproval(
                tool_use_id=tool_use_id, payload=payload, request_id=request_id
            )
            await self._emit_tool_diff_preview(state, payload)
            # AskUserQuestion's tool_call event already renders the question
            # UI in the transcript via parseAskUserQuestion; emitting a
            # separate APPROVAL_REQUEST card would show the prompt twice. Only
            # register the pending entry so respond_to_ask_question answers it.
            if tool_name != "AskUserQuestion":
                await self._emit_event(
                    state.session_id,
                    EventKind.APPROVAL_REQUEST,
                    format_approval_text(payload),
                    {
                        "tool_name": tool_name,
                        "tool_input": payload["tool_input"],
                        "approval_id": tool_use_id,
                        "method": "can_use_tool",
                        "status": SessionStatus.WAITING_INPUT,
                        **(
                            {
                                "permission_suggestions": payload[
                                    "permission_suggestions"
                                ]
                            }
                            if payload.get("permission_suggestions")
                            else {}
                        ),
                        **(
                            {"diff_preview": payload["diff_preview"]}
                            if isinstance(payload.get("diff_preview"), dict)
                            else {}
                        ),
                    },
                    SessionStatus.WAITING_INPUT,
                )

    async def _write_control_response(
        self,
        state: ClaudeSessionState,
        request_id: object,
        response_body: dict[str, Any],
    ) -> None:
        """Write a ``control_response`` envelope to the binary's stdin."""
        if not isinstance(request_id, str):
            return
        if state.process.stdin is None or state.process.stdin.is_closing():
            return
        envelope = {
            "type": "control_response",
            "response": {"request_id": request_id, **response_body},
        }
        line = (json.dumps(envelope) + "\n").encode("utf-8")
        state.process.stdin.write(line)
        with suppress(BrokenPipeError, ConnectionResetError):
            await state.process.stdin.drain()

    async def _write_permission_response(
        self,
        state: ClaudeSessionState,
        request_id: object,
        decision: str,
        tool_input: dict[str, Any] | None,
        reason: str = "",
        permission_updates: list[dict[str, Any]] | None = None,
    ) -> None:
        """Answer a ``can_use_tool`` request with an allow/deny PermissionResult."""
        if decision == "allow":
            result: dict[str, Any] = {
                "behavior": "allow",
                "updatedInput": tool_input or {},
            }
            if permission_updates:
                result["permission_updates"] = permission_updates
        else:
            result = {"behavior": "deny", "message": reason or "denied by user"}
        await self._write_control_response(
            state, request_id, {"subtype": "success", "response": result}
        )

    def _diff_preview_from_input(
        self,
        tool_name: object,
        tool_input: dict[str, Any] | None,
        cwd: str,
    ) -> dict[str, Any] | None:
        if tool_name not in ("Edit", "Write", "MultiEdit") or tool_input is None:
            return None
        path_text = tool_input.get("file_path") or tool_input.get("path")
        if not isinstance(path_text, str) or not path_text:
            return None
        # Read the current file for a full-context diff when it's reachable
        # (local sessions). For remote sessions the file lives on the SSH
        # target and isn't readable here, so fall back to a diff synthesized
        # from the tool input itself.
        resolved = Path(path_text).expanduser()
        if not resolved.is_absolute() and cwd:
            resolved = Path(cwd).expanduser() / resolved
        old = ""
        readable = False
        try:
            if resolved.exists():
                old = resolved.read_text(encoding="utf-8")
                readable = True
        except OSError:
            readable = False
        try:
            change_type: ChangeType
            if tool_name == "Write":
                new = str(tool_input.get("content") or "")
                change_type = "update" if readable and old else "add"
                if not readable:
                    old = ""
            elif tool_name == "Edit":
                if readable:
                    new = _apply_edit(old, tool_input)
                else:
                    old = str(tool_input.get("old_string") or "")
                    new = str(tool_input.get("new_string") or "")
                change_type = "update"
            else:  # MultiEdit
                edits = [
                    e for e in (tool_input.get("edits") or []) if isinstance(e, dict)
                ]
                if readable:
                    new = old
                    for edit in edits:
                        new = _apply_edit(new, edit)
                else:
                    old = "\n".join(str(e.get("old_string") or "") for e in edits)
                    new = "\n".join(str(e.get("new_string") or "") for e in edits)
                change_type = "update"
        except ValueError as exc:
            preview = build_preview("proposed", [unavailable_file(path_text, str(exc))])
            return preview.model_dump(mode="json") if preview else None
        preview = build_preview(
            "proposed", [file_from_old_new(path_text, old, new, change_type)]
        )
        return preview.model_dump(mode="json") if preview else None

    async def _emit_tool_diff_preview(
        self, state: ClaudeSessionState, payload: dict[str, Any]
    ) -> None:
        tool_use_id = str(payload.get("tool_use_id") or "")
        if not tool_use_id or tool_use_id in state.emitted_diff_preview_tool_ids:
            return
        diff_preview = payload.get("diff_preview")
        if not isinstance(diff_preview, dict):
            return
        tool_name = str(payload.get("tool_name") or "tool")
        if tool_name not in {"Edit", "Write", "MultiEdit"}:
            return
        state.emitted_diff_preview_tool_ids.add(tool_use_id)
        await self._emit_event(
            state.session_id,
            EventKind.TOOL_RESULT,
            f"{tool_name} diff preview",
            {
                "method": "can_use_tool.diff_preview",
                "item_id": tool_use_id,
                "tool_name": tool_name,
                "tool_input": payload.get("tool_input"),
                "tool_use_id": tool_use_id,
                "diff_preview": diff_preview,
                "status": SessionStatus.RUNNING,
            },
            SessionStatus.RUNNING,
        )

    def has_pending_approval(self, session_id: str) -> bool:
        state = self._sessions.get(session_id)
        return bool(state and state.pending)

    def pending_approval_ids(self, session_id: str) -> tuple[str, ...]:
        state = self._sessions.get(session_id)
        if state is None:
            return ()
        return tuple(state.pending.keys())

    def session_permission_mode(self, session_id: str) -> str | None:
        """Read the binary's currently-active permission mode for a session.

        The adapter flips this internally after an ExitPlanMode approval
        (via the set_permission_mode control_request), so callers that
        persist the mode separately (storage, broadcast) can pick the
        change up here.
        """
        state = self._sessions.get(session_id)
        return state.permission_mode if state is not None else None

    async def terminate_session(self, session_id: str) -> bool:
        state = self._sessions.pop(session_id, None)
        if state is None:
            return False
        if not state.task_tracker.is_empty:
            # A respawn (set_effort, reattach) terminates before the
            # resume-spawn; preserve the folded todo state for it to restore.
            self._carried_task_state[session_id] = (
                state.task_tracker,
                state.task_card_item_id,
            )
        state.closing = True
        if state.rate_limit_refresh_task is not None:
            state.rate_limit_refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await state.rate_limit_refresh_task
        # Deny any pending approvals so the binary unblocks the parked tool
        # call before we tear the process down (best-effort; the stdin may
        # already be gone).
        for pending in list(state.pending.values()):
            await self._write_permission_response(
                state, pending.request_id, "deny", None, "session terminated"
            )
        state.pending.clear()
        # Cancel any in-flight control_request awaiters so set_permission_mode
        # callers don't hang past session shutdown.
        for control_future in list(state.pending_controls.values()):
            if not control_future.done():
                control_future.cancel()
        state.pending_controls.clear()
        if state.process.stdin is not None and not state.process.stdin.is_closing():
            with suppress(Exception):
                state.process.stdin.close()
        if state.process.returncode is None:
            with suppress(ProcessLookupError):
                state.process.terminate()
            try:
                await asyncio.wait_for(state.process.wait(), timeout=5.0)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    state.process.kill()
                with suppress(Exception):
                    await state.process.wait()
        for task in (state.stdout_task, state.stderr_task, state.wait_task):
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        return True

    async def shutdown(self) -> None:
        for session_id in list(self._sessions.keys()):
            await self.terminate_session(session_id)

    async def _spawn(
        self,
        session_id: str,
        cwd: str,
        claude_session_id: str,
        resume: bool,
        launch_factory_override: LaunchFactory | None = None,
        permission_mode: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        custom_args: list[str] | None = None,
        fork_from_claude_session_id: str | None = None,
        launch_env: dict[str, str] | None = None,
    ) -> ClaudeSessionState:
        resolved_mode = (
            permission_mode if permission_mode in CLAUDE_PERMISSION_MODES else "default"
        )
        cli_mode = claude_cli_mode_for(resolved_mode)
        effective_custom_args = custom_args or []
        effective_launch_env = launch_env or {}
        launch_factory = launch_factory_override or self._launch_factory
        if launch_factory is None:
            spec = self._build_local_launch_spec(
                session_id,
                cwd,
                claude_session_id,
                resume,
                cli_mode,
                model,
                effort,
                effective_custom_args,
                fork_from_claude_session_id,
                effective_launch_env,
            )
        else:
            spec = launch_factory(
                session_id,
                cwd,
                claude_session_id,
                resume,
                cli_mode,
                model,
                effort,
                effective_custom_args,
                fork_from_claude_session_id,
                effective_launch_env,
            )
        process = await asyncio.create_subprocess_exec(
            *spec.args,
            cwd=spec.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=spec.env,
            limit=CLAUDE_STREAM_BUFFER_LIMIT,
        )
        state = ClaudeSessionState(
            session_id=session_id,
            cwd=cwd,
            process=process,
            claude_session_id=claude_session_id,
            stdout_task=asyncio.create_task(asyncio.sleep(0)),  # placeholder
            stderr_task=asyncio.create_task(asyncio.sleep(0)),
            wait_task=asyncio.create_task(asyncio.sleep(0)),
            permission_mode=resolved_mode,
            model=self._effective_model_id(model),
            effort=effort or None,
            custom_args=list(effective_custom_args),
            launch_factory=launch_factory,
            launch_env=dict(effective_launch_env),
        )
        state.stdout_task = asyncio.create_task(self._read_stdout(state))
        state.stderr_task = asyncio.create_task(self._read_stderr(state))
        state.wait_task = asyncio.create_task(self._watch_process(state))
        if resume:
            self._restore_carried_task_state(session_id, state)
        else:
            # A fresh (non-resume) start of this id isn't a respawn; drop any
            # stale carry so it can't leak into an unrelated session.
            self._carried_task_state.pop(session_id, None)
        self._sessions[session_id] = state
        return state

    def _restore_carried_task_state(
        self, session_id: str, state: ClaudeSessionState
    ) -> None:
        # A resume/respawn builds a fresh state, but the CLI keeps the same task
        # ids and will send TaskUpdate deltas for tasks created before the
        # respawn. Without the prior tracker those deltas hit unknown ids and
        # materialise blank stubs under a new card, so restore the fold that
        # terminate_session stashed just before this respawn.
        carried = self._carried_task_state.pop(session_id, None)
        if carried is None:
            return
        state.task_tracker, state.task_card_item_id = carried

    def discard_session(self, session_id: str) -> None:
        # Permanent removal: drop the carried todo tracker so a stash for a
        # session that's deleted (rather than respawned) doesn't linger. An
        # exited session keeps its stash — it may still be reattached, which is
        # exactly when the carry-over is needed.
        self._carried_task_state.pop(session_id, None)

    def _build_local_launch_spec(
        self,
        session_id: str,
        cwd: str,
        claude_session_id: str,
        resume: bool,
        cli_mode: str,
        model: str | None = None,
        effort: str | None = None,
        custom_args: list[str] | None = None,
        fork_from_claude_session_id: str | None = None,
        launch_env: dict[str, str] | None = None,
    ) -> ClaudeLaunchSpec:
        binary = self._binary or shutil.which("claude")
        if binary is None:
            raise ClaudeCliError("claude binary not found on PATH")
        cwd_path = Path(cwd).expanduser()
        args = [
            binary,
            "-p",
            "--input-format=stream-json",
            "--output-format=stream-json",
            "--include-hook-events",
            "--verbose",
            # Route tool permission prompts over the stdio control protocol as
            # `can_use_tool` control_requests (valid only with `-p`). This is
            # the single approval channel — see `_handle_can_use_tool`.
            "--permission-prompt-tool",
            "stdio",
            "--permission-mode",
            cli_mode,
        ]
        if model:
            args.extend(["--model", model])
        if effort:
            args.extend(["--effort", effort])
        if fork_from_claude_session_id:
            args.extend(
                [
                    "--resume",
                    fork_from_claude_session_id,
                    "--fork-session",
                    "--session-id",
                    claude_session_id,
                ]
            )
        elif resume:
            args.extend(["--resume", claude_session_id])
        else:
            args.extend(["--session-id", claude_session_id])
        if custom_args:
            args.extend(custom_args)
        env = {
            **os.environ,
            **(launch_env or {}),
            # Enable the dynamic-workflow feature so the Workflow tool is
            # available and routes its approval through `can_use_tool`.
            "CLAUDE_CODE_WORKFLOWS": "1",
            # Let an agent (and the waypoint CLI it runs) know its own session,
            # so child sessions it spawns can inherit this session's posture.
            "WAYPOINT_SESSION_ID": session_id,
        }
        return ClaudeLaunchSpec(
            args=args,
            cwd=str(cwd_path) if cwd_path.exists() else None,
            env=env,
        )

    async def _read_stdout(self, state: ClaudeSessionState) -> None:
        assert state.process.stdout is not None
        try:
            while True:
                # A dropped line is almost always a giant tool-result blob we
                # couldn't have parsed anyway; the session survives it.
                line = await _read_stream_line(
                    state.process.stdout, state.session_id, "stdout"
                )
                if line is None:
                    continue
                if not line:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                except json.JSONDecodeError:
                    log.warning(
                        "claude stdout line not JSON",
                        extra={
                            "session_id": state.session_id,
                            "raw": stripped[:200].decode(errors="replace"),
                        },
                    )
                    continue
                await self._dispatch(state, event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "claude stdout reader failed", extra={"session_id": state.session_id}
            )
            if not state.closing:
                await self._emit_event(
                    state.session_id,
                    EventKind.SYSTEM_NOTE,
                    "Claude stdout reader failed",
                    {"status": SessionStatus.ERROR},
                    SessionStatus.ERROR,
                )

    async def _read_stderr(self, state: ClaudeSessionState) -> None:
        assert state.process.stderr is not None
        try:
            while True:
                line = await _read_stream_line(
                    state.process.stderr, state.session_id, "stderr"
                )
                if line is None:
                    continue
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    state.stderr_tail.append(text)
                    log.info(
                        "claude stderr",
                        extra={"session_id": state.session_id, "line": text},
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "claude stderr reader failed", extra={"session_id": state.session_id}
            )

    async def _watch_process(self, state: ClaudeSessionState) -> None:
        try:
            returncode = await state.process.wait()
        except asyncio.CancelledError:
            raise
        # Drain stderr/stdout readers so the tail captures the final lines.
        for task in (state.stderr_task, state.stdout_task):
            with suppress(asyncio.CancelledError, Exception):
                await task
        if state.closing:
            return
        text = self._format_dead_process_error(state, returncode)
        await self._emit_event(
            state.session_id,
            EventKind.SYSTEM_NOTE,
            text,
            {
                "method": "process.exit",
                "returncode": returncode,
                "stderr_tail": list(state.stderr_tail),
                "status": SessionStatus.ERROR,
            },
            SessionStatus.ERROR,
        )

    def _format_dead_process_error(
        self, state: ClaudeSessionState, returncode: int | None
    ) -> str:
        rc_text = (
            "still running but stdin is closed"
            if returncode is None
            else f"rc={returncode}"
        )
        header = f"Claude process exited ({rc_text})"
        tail = "\n".join(state.stderr_tail)
        if not tail:
            return header
        return f"{header}\n--- stderr tail ---\n{tail}"

    async def _dispatch(self, state: ClaudeSessionState, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "control_response":
            self._handle_control_response(state, event)
            return
        if event_type == "control_request":
            request = event.get("request") or {}
            if request.get("subtype") == "can_use_tool":
                await self._handle_can_use_tool(state, event)
            else:
                # Acknowledge other inbound control_requests so the binary
                # doesn't park waiting on us; we don't implement them.
                await self._write_control_response(
                    state,
                    event.get("request_id"),
                    {"subtype": "error", "error": "unsupported control_request"},
                )
            return
        if event_type == "system":
            await self._handle_system(state, event)
            return
        if event_type == "assistant":
            await self._handle_assistant(state, event)
            return
        if event_type == "user":
            await self._handle_user(state, event)
            return
        if event_type == "result":
            await self._handle_result(state, event)
            return
        if event_type == "rate_limit_event":
            await self._emit_event(
                state.session_id,
                EventKind.SYSTEM_NOTE,
                format_rate_limit(event.get("rate_limit_info", {})),
                {
                    "method": "rate_limit_event",
                    "payload": event,
                    "status": SessionStatus.RUNNING,
                },
                SessionStatus.RUNNING,
            )
            return
        # Unknown — surface as system_note for visibility.
        await self._emit_event(
            state.session_id,
            EventKind.SYSTEM_NOTE,
            f"Unhandled claude event: {event_type}",
            {
                "method": event_type or "unknown",
                "payload": event,
                "status": SessionStatus.RUNNING,
            },
            SessionStatus.RUNNING,
        )

    async def _handle_system(
        self, state: ClaudeSessionState, event: dict[str, Any]
    ) -> None:
        subtype = event.get("subtype")
        if subtype == "init":
            slash_commands = event.get("slash_commands")
            if isinstance(slash_commands, list):
                state.slash_commands = tuple(
                    command
                    for command in slash_commands
                    if isinstance(command, str) and command
                )
            model = self._effective_model_id(event.get("model"))
            if model is not None:
                current_family = claude_model_family(state.model)
                incoming_family = claude_model_family(model)
                if state.model is None or current_family != incoming_family:
                    state.model = model
                    await self._refresh_context_usage(state)
            raw_model = event.get("model")
            if (
                isinstance(raw_model, str)
                and raw_model
                and raw_model != state.resolved_model
            ):
                state.resolved_model = raw_model
                if self._on_session_update is not None:
                    await self._on_session_update(
                        state.session_id, {"resolved_model": raw_model}, False
                    )
            if self._on_init is not None:
                self._on_init(state.session_id, event)
            # Claude's stream-json mode emits `init` at the start of every
            # `--print` run, which here means once per user turn (not just at
            # session start). Tagging this with IDLE downgrades the freshly
            # set RUNNING status from handle_input and drops the spinner
            # until the first content chunk lands. Mark it RUNNING — by the
            # time init fires, the binary is already processing input.
            await self._emit_event(
                state.session_id,
                EventKind.SYSTEM_NOTE,
                f"Claude session ready (model {event.get('model', 'unknown')})",
                {
                    "method": "system.init",
                    "payload": event,
                    "status": SessionStatus.RUNNING,
                },
                SessionStatus.RUNNING,
            )
            return
        if subtype in {"hook_started", "hook_response"}:
            # Surface only deny outcomes; we already emit APPROVAL_REQUEST events of our own.
            if subtype == "hook_response":
                stdout = event.get("stdout") or ""
                if "permissionDecision" not in stdout:
                    return
                # Decision flowed through; do not emit anything extra.
            return
        if subtype == "status":
            # /compact and similar CLI commands surface their lifecycle here.
            text, status = format_status_event(event)
            if text:
                await self._emit_event(
                    state.session_id,
                    EventKind.SYSTEM_NOTE,
                    text,
                    {
                        "method": "system.status",
                        "payload": event,
                        "status": status,
                    },
                    status,
                )
            return
        if subtype == "compact_boundary":
            metadata = event.get("compact_metadata") or {}
            await self._emit_event(
                state.session_id,
                EventKind.SYSTEM_NOTE,
                format_compact_boundary(metadata),
                {
                    "method": "system.compact_boundary",
                    "payload": event,
                    "status": SessionStatus.IDLE,
                },
                SessionStatus.IDLE,
            )
            return
        # Anything else — stash as system note.
        await self._emit_event(
            state.session_id,
            EventKind.SYSTEM_NOTE,
            f"system/{subtype}",
            {
                "method": f"system.{subtype}",
                "payload": event,
                "status": SessionStatus.RUNNING,
            },
            SessionStatus.RUNNING,
        )

    async def _handle_assistant(
        self, state: ClaudeSessionState, event: dict[str, Any]
    ) -> None:
        message = event.get("message") or {}
        message_id = str(message.get("id") or "")
        usage = message.get("usage") or event.get("usage") or {}
        for block in iter_content_blocks(message.get("content")):
            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text") or ""
                if not text:
                    continue
                await self._emit_event(
                    state.session_id,
                    EventKind.AGENT_OUTPUT,
                    text,
                    {
                        "method": "assistant.text",
                        "item_id": message_id,
                        "payload": block,
                        "status": SessionStatus.RUNNING,
                    },
                    SessionStatus.RUNNING,
                )
            elif block_type == "tool_use":
                tool_use_id = str(block.get("id") or "")
                tool_name = block.get("name") or "tool"
                if tool_name == "ExitPlanMode":
                    # The plan is rendered as a markdown agent_output text
                    # block above and the approval card represents the gate —
                    # an extra tool_call disclosure with the JSON payload is
                    # noise. Suppress the echoed tool_result too (our verdict
                    # message) so it doesn't render as an orphan tool run.
                    if tool_use_id:
                        state.suppressed_result_tool_use_ids.add(tool_use_id)
                    continue
                if tool_name in TASK_TOOL_NAMES:
                    await self._handle_task_tool_use(
                        state, tool_name, tool_use_id, block.get("input") or {}
                    )
                    continue
                input_text = json.dumps(block.get("input") or {}, indent=2)
                await self._emit_event(
                    state.session_id,
                    EventKind.TOOL_CALL,
                    f"{tool_name}\n{input_text}",
                    {
                        "method": "assistant.tool_use",
                        "item_id": tool_use_id,
                        "tool_name": tool_name,
                        "tool_use_id": tool_use_id,
                        "payload": block,
                        "status": SessionStatus.RUNNING,
                    },
                    SessionStatus.RUNNING,
                )
            elif block_type == "thinking":
                # Optional surface; hide behind an opt-in later if too noisy.
                continue
        snapshot = _context_usage_snapshot_from_message(
            state.model,
            usage if isinstance(usage, dict) else {},
        )
        if snapshot is not None:
            state.context_usage_snapshot = snapshot
            await self._publish_context_usage(state, snapshot)

    async def _handle_user(
        self, state: ClaudeSessionState, event: dict[str, Any]
    ) -> None:
        message = event.get("message") or {}
        for block in iter_content_blocks(message.get("content")):
            if block.get("type") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id") or "")
            if tool_use_id and tool_use_id in state.pending_task_creates:
                # The TaskCreate result carries the assigned task id; fold it
                # into the todo snapshot instead of rendering the raw result.
                await self._handle_task_create_result(state, tool_use_id, block)
                continue
            if tool_use_id and tool_use_id in state.suppressed_result_tool_use_ids:
                # Echoed verdict for an ExitPlanMode approval, or a suppressed
                # Task tool result (TaskUpdate/TaskGet/TaskList) whose effect is
                # already reflected in the todo card. The plan card and the
                # agent's next message convey ExitPlanMode outcomes.
                state.suppressed_result_tool_use_ids.discard(tool_use_id)
                continue
            content = block.get("content")
            text = stringify_tool_result(content)
            is_error = bool(block.get("is_error"))
            kind = EventKind.TOOL_RESULT
            status = SessionStatus.RUNNING
            metadata = {
                "method": "user.tool_result",
                "item_id": tool_use_id,
                "tool_use_id": tool_use_id,
                "is_error": is_error,
                "payload": block,
                "status": status,
            }
            if tool_use_id:
                preview_metadata = state.file_edit_preview_metadata.pop(
                    tool_use_id, None
                )
                if preview_metadata is not None:
                    metadata.update(preview_metadata)
            await self._emit_event(state.session_id, kind, text, metadata, status)

    async def _handle_task_tool_use(
        self,
        state: ClaudeSessionState,
        tool_name: str,
        tool_use_id: str,
        tool_input: dict[str, Any],
    ) -> None:
        if tool_name == "TaskCreate":
            # The assigned id only comes back in the matching tool_result, so
            # stash the input until then; the result handler folds it in.
            if tool_use_id:
                state.pending_task_creates[tool_use_id] = tool_input
            return
        # Drop the echoed result for the remaining Task tools — their effect is
        # already reflected in the todo card (TaskUpdate) or read-only
        # (TaskGet/TaskList). We deliberately do not reseed the tracker from
        # TaskList results: persisted production data shows TaskGet/TaskList go
        # effectively unused, so their result schema is unverified and
        # reconciling against the doc's shape would risk clobbering good state.
        # Resume is instead covered by stub-materialisation in
        # TaskListTracker.update for updates that reference an unseen id.
        if tool_use_id:
            state.suppressed_result_tool_use_ids.add(tool_use_id)
        if tool_name == "TaskUpdate":
            task_id = str(tool_input.get("taskId") or "")
            if not task_id:
                return
            state.task_tracker.update(
                task_id,
                status=tool_input.get("status"),
                content=tool_input.get("subject"),
                active_form=tool_input.get("activeForm"),
                description=tool_input.get("description"),
            )
            await self._emit_task_snapshot(state)

    async def _handle_task_create_result(
        self, state: ClaudeSessionState, tool_use_id: str, block: dict[str, Any]
    ) -> None:
        create_input = state.pending_task_creates.pop(tool_use_id, None)
        if create_input is None:
            return
        task_id = extract_created_task_id(block)
        if task_id is None:
            # Fall back to the tool_use_id so the item still renders; a later
            # TaskUpdate keyed by the real id will then create a stub instead of
            # patching this one.
            log.debug("TaskCreate result missing task id; falling back to tool_use_id")
            task_id = tool_use_id
        if state.task_tracker.is_empty:
            # A fresh task group — rotate to a new card so it doesn't overwrite
            # a prior, completed group's card. CC numbers task ids monotonically
            # within a session (and never deletes in the observed data), so the
            # tracker is genuinely empty here and reused ids can't collide.
            state.task_card_item_id = None
        state.task_tracker.create(
            task_id,
            content=str(create_input.get("subject") or ""),
            active_form=create_input.get("activeForm"),
            description=create_input.get("description"),
            status=create_input.get("status") or "pending",
        )
        await self._emit_task_snapshot(state)

    async def _emit_task_snapshot(self, state: ClaudeSessionState) -> None:
        if state.task_card_item_id is None:
            state.task_card_item_id = uuid.uuid4().hex
        todos = state.task_tracker.snapshot()
        await self._emit_event(
            state.session_id,
            EventKind.TOOL_RESULT,
            format_task_snapshot(todos),
            {
                "method": "assistant.task_update",
                "item_id": state.task_card_item_id,
                "item_type": "todo_list",
                "tool_name": "TodoWrite",
                "payload": {"input": {"todos": todos}},
                "status": SessionStatus.RUNNING,
            },
            SessionStatus.RUNNING,
        )

    async def _handle_result(
        self, state: ClaudeSessionState, event: dict[str, Any]
    ) -> None:
        subtype = event.get("subtype", "")
        is_error = bool(event.get("is_error"))
        usage = event.get("usage") or {}
        cost = event.get("total_cost_usd")
        denials = event.get("permission_denials") or []
        text_parts = [f"Turn {subtype}".strip() or "Turn complete"]
        if cost is not None:
            text_parts.append(f"cost ${cost:.4f}")
        if usage.get("output_tokens"):
            text_parts.append(f"{usage['output_tokens']} output tokens")
        if denials:
            text_parts.append(f"{len(denials)} permission denial(s)")
        text = " · ".join(text_parts)
        await self._emit_event(
            state.session_id,
            EventKind.SYSTEM_NOTE,
            text,
            {
                "method": "result",
                "payload": event,
                "status": SessionStatus.ERROR if is_error else SessionStatus.IDLE,
            },
            SessionStatus.ERROR if is_error else SessionStatus.IDLE,
        )

    async def _publish_context_usage(
        self, state: ClaudeSessionState, snapshot: SessionContextUsage
    ) -> None:
        signature = (snapshot.used_tokens, snapshot.context_window_tokens)
        if state.context_usage_signature == signature:
            return
        state.context_usage_signature = signature
        if self._on_session_update is None:
            return
        await self._on_session_update(
            state.session_id,
            {"context_usage": snapshot.model_dump(mode="json")},
            False,
        )

    async def _refresh_rate_limit_usage_loop(
        self, state: ClaudeSessionState, *, refresh_interval_seconds: float
    ) -> None:
        try:
            while state.session_id in self._sessions:
                await self._refresh_rate_limit_usage(state)
                await asyncio.sleep(refresh_interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception(
                "claude rate-limit refresh loop failed",
                extra={"session_id": state.session_id},
            )

    async def _refresh_rate_limit_usage(self, state: ClaudeSessionState) -> None:
        probe = state.rate_limit_probe
        if probe is None:
            return
        try:
            snapshot = await probe()
        except Exception:  # noqa: BLE001
            log.exception(
                "claude rate-limit probe failed",
                extra={"session_id": state.session_id},
            )
            return
        if snapshot is None:
            return
        await self._publish_rate_limit_usage(state, snapshot)

    async def _publish_rate_limit_usage(
        self, state: ClaudeSessionState, snapshot: SessionRateLimitUsage
    ) -> None:
        signature = json.dumps(snapshot.model_dump(mode="json"), sort_keys=True)
        if state.rate_limit_usage_signature == signature:
            return
        state.rate_limit_usage_signature = signature
        state.rate_limit_usage_snapshot = snapshot
        if self._on_session_update is None:
            return
        await self._on_session_update(
            state.session_id,
            {"rate_limit_usage": snapshot.model_dump(mode="json")},
            True,
        )

    async def _refresh_context_usage(self, state: ClaudeSessionState) -> None:
        snapshot = state.context_usage_snapshot
        if snapshot is None:
            return
        model = state.model or self._default_model_id
        if model is None:
            return
        context_window_tokens = claude_context_window_for_model(model)
        if context_window_tokens is None:
            return
        if snapshot.context_window_tokens == context_window_tokens:
            return
        # Bump only the window here; used_tokens is recomputed from the next
        # assistant turn under the active model.
        refreshed = snapshot.model_copy(
            update={"context_window_tokens": context_window_tokens}
        )
        state.context_usage_snapshot = refreshed
        await self._publish_context_usage(state, refreshed)

    def _map_decision(self, decision: str) -> str:
        return "allow" if is_approve_decision(decision) else "deny"

    def _require_session(self, session_id: str) -> ClaudeSessionState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ClaudeCliError(f"claude session not active: {session_id}") from exc

    def _effective_model_id(self, model: str | None) -> str | None:
        normalized = normalize_claude_model_id(model)
        if normalized is not None:
            return normalized
        return self._default_model_id


def _context_usage_snapshot_from_message(
    model: str | None, usage: dict[str, Any]
) -> SessionContextUsage | None:
    input_tokens = _non_negative_int(usage.get("input_tokens"))
    cache_read_input_tokens = _non_negative_int(usage.get("cache_read_input_tokens"))
    cache_creation_input_tokens = _non_negative_int(
        usage.get("cache_creation_input_tokens")
    )
    output_tokens = _non_negative_int(usage.get("output_tokens"))

    used_tokens = sum(
        value
        for value in (
            input_tokens,
            cache_read_input_tokens,
            cache_creation_input_tokens,
        )
        if value is not None
    )
    if used_tokens <= 0:
        return None

    context_window_tokens = claude_context_window_for_model(model)
    if context_window_tokens is None:
        return None

    breakdown = {
        key: value
        for key, value in {
            "input_tokens": input_tokens,
            "cache_read_tokens": cache_read_input_tokens,
            "cache_creation_tokens": cache_creation_input_tokens,
            "output_tokens": output_tokens,
        }.items()
        if value is not None
    }
    return SessionContextUsage(
        used_tokens=used_tokens,
        context_window_tokens=context_window_tokens,
        updated_at=datetime.now(UTC),
        source="claude_code",
        breakdown=breakdown,
    )


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float) and value.is_integer():
        return max(0, int(value))
    return None
