from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from collections import deque
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from waypoint.schemas import EventKind, SessionStatus

log = logging.getLogger("waypoint.claude_cli")

CONTROL_REQUEST_TIMEOUT_SECONDS = 10.0
CLAUDE_PERMISSION_MODES = (
    "default",
    "plan",
    "acceptEdits",
    "auto",
    "bypassPermissions",
    "dontAsk",
)
# Modes that bypass Waypoint's PreToolUse approval card entirely.
CLAUDE_AUTO_APPROVE_MODES = frozenset({"auto", "bypassPermissions", "dontAsk"})
# Tools acceptEdits auto-approves; everything else still surfaces the card.
CLAUDE_ACCEPT_EDITS_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


def _auto_approve_for_mode(mode: str, tool_name: object) -> dict[str, str] | None:
    if mode in CLAUDE_AUTO_APPROVE_MODES:
        return {
            "permissionDecision": "allow",
            "permissionDecisionReason": f"auto-approved by Waypoint mode={mode}",
        }
    if mode == "acceptEdits" and tool_name in CLAUDE_ACCEPT_EDITS_TOOLS:
        return {
            "permissionDecision": "allow",
            "permissionDecisionReason": "auto-approved by Waypoint mode=acceptEdits",
        }
    return None


EmitEvent = Callable[
    [str, EventKind, str, dict[str, Any], SessionStatus],
    Coroutine[Any, Any, None],
]
LaunchFactory = Callable[
    [str, str, str, bool],
    "ClaudeLaunchSpec",
]

# Tools we surface to the user for approval. Other tools (Read, Grep, Glob, ...)
# are left to Claude's own permission policy.
GATED_TOOLS = (
    "Bash",
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "Task",
    "WebFetch",
    "WebSearch",
    # ExitPlanMode is the tool Claude calls to present a plan in plan mode —
    # the user must approve it before plan mode actually exits. Must mirror
    # claude_runtime.GATED_TOOLS_REGEX (the regex written into the hook
    # settings file at session start).
    "ExitPlanMode",
)
GATED_TOOLS_REGEX = "^(?:" + "|".join(GATED_TOOLS) + ")$"

DEFAULT_TIMEOUT_SECONDS = 300.0

STDERR_TAIL_LINES = 50


@dataclass
class ClaudePendingApproval:
    tool_use_id: str
    payload: dict[str, Any]
    future: asyncio.Future[dict[str, str]]


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
    last_message_text: dict[str, str] = field(default_factory=dict)
    terminal_fragments: list[str] = field(default_factory=list)
    stderr_tail: deque[str] = field(
        default_factory=lambda: deque(maxlen=STDERR_TAIL_LINES)
    )
    pending_controls: dict[str, asyncio.Future[dict[str, Any]]] = field(
        default_factory=dict
    )
    permission_mode: str = "default"
    closing: bool = False


class ClaudeCliError(RuntimeError):
    pass


class ClaudeCliAdapter:
    def __init__(
        self,
        emit_event: EmitEvent,
        hook_settings_path: Path,
        hook_secret: str,
        hook_url: str,
        binary: str | None = None,
        permission_mode: str = "default",
        launch_factory: LaunchFactory | None = None,
    ) -> None:
        self._emit_event = emit_event
        self._hook_settings_path = hook_settings_path
        self._hook_secret = hook_secret
        self._hook_url = hook_url
        self._binary = binary
        self._permission_mode = permission_mode
        self._launch_factory = launch_factory
        self._sessions: dict[str, ClaudeSessionState] = {}
        self._approval_lock = asyncio.Lock()

    async def start_session(
        self,
        session_id: str,
        cwd: str,
        claude_session_id: str,
        launch_factory_override: LaunchFactory | None = None,
        permission_mode: str | None = None,
    ) -> str:
        state = await self._spawn(
            session_id,
            cwd,
            claude_session_id,
            resume=False,
            launch_factory_override=launch_factory_override,
            permission_mode=permission_mode,
        )
        return state.claude_session_id

    async def restore_session(
        self,
        session_id: str,
        cwd: str,
        claude_session_id: str,
        launch_factory_override: LaunchFactory | None = None,
        permission_mode: str | None = None,
    ) -> None:
        await self._spawn(
            session_id,
            cwd,
            claude_session_id,
            resume=True,
            launch_factory_override=launch_factory_override,
            permission_mode=permission_mode,
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
        # Mirror the new mode locally so await_approval can short-circuit
        # without consulting the database on every PreToolUse hit.
        state = self._sessions.get(session_id)
        if state is not None:
            state.permission_mode = mode

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

    async def send_input(self, session_id: str, text: str) -> None:
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
            "message": {"role": "user", "content": text},
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
        with suppress(ProcessLookupError):
            state.process.send_signal(2)  # SIGINT

    async def respond_to_approval(self, session_id: str, decision: str) -> bool:
        state = self._sessions.get(session_id)
        if state is None or not state.pending:
            return False
        # Resolve oldest pending first.
        tool_use_id, pending = next(iter(state.pending.items()))
        mapped = self._map_decision(decision)
        if not pending.future.done():
            pending.future.set_result(
                {
                    "permissionDecision": mapped,
                    "permissionDecisionReason": (
                        "approved by Waypoint user"
                        if mapped == "allow"
                        else "denied by Waypoint user"
                    ),
                }
            )
        state.pending.pop(tool_use_id, None)
        return True

    async def await_approval(self, payload: dict[str, Any]) -> dict[str, str]:
        waypoint_session_id = str(payload.get("waypoint_session_id") or "")
        tool_use_id = str(payload.get("tool_use_id") or "")
        if not waypoint_session_id or not tool_use_id:
            return {
                "permissionDecision": "ask",
                "permissionDecisionReason": "missing identifiers",
            }
        tool_name = payload.get("tool_name")
        async with self._approval_lock:
            state = self._sessions.get(waypoint_session_id)
            if state is None:
                return {
                    "permissionDecision": "ask",
                    "permissionDecisionReason": "session not active",
                }
            # Honor the session's permission_mode at the hook layer. Claude's
            # permission_mode is an internal hint that only kicks in when no
            # hook is wired; with Waypoint's PreToolUse hook always installed,
            # the mode never gets consulted unless we do it here.
            auto = _auto_approve_for_mode(state.permission_mode, tool_name)
            if auto is not None:
                return auto
            if tool_use_id in state.pending:
                # Hook was retried for the same tool call. Reuse the existing future.
                pending = state.pending[tool_use_id]
            else:
                future: asyncio.Future[dict[str, str]] = (
                    asyncio.get_running_loop().create_future()
                )
                pending = ClaudePendingApproval(
                    tool_use_id=tool_use_id, payload=payload, future=future
                )
                state.pending[tool_use_id] = pending
                await self._emit_event(
                    waypoint_session_id,
                    EventKind.APPROVAL_REQUEST,
                    self._format_approval_text(payload),
                    {
                        "tool_name": payload.get("tool_name"),
                        "tool_input": payload.get("tool_input"),
                        "tool_use_id": tool_use_id,
                        "method": "PreToolUse",
                        "status": SessionStatus.WAITING_INPUT,
                    },
                    SessionStatus.WAITING_INPUT,
                )
        try:
            decision = await asyncio.wait_for(
                pending.future, timeout=DEFAULT_TIMEOUT_SECONDS
            )
        except TimeoutError:
            decision = {
                "permissionDecision": "deny",
                "permissionDecisionReason": "Waypoint approval timed out",
            }
            state.pending.pop(tool_use_id, None)
        return decision

    def has_pending_approval(self, session_id: str) -> bool:
        state = self._sessions.get(session_id)
        return bool(state and state.pending)

    async def terminate_session(self, session_id: str) -> bool:
        state = self._sessions.pop(session_id, None)
        if state is None:
            return False
        state.closing = True
        # Resolve any pending approvals as deny so the hook unblocks.
        for pending in list(state.pending.values()):
            if not pending.future.done():
                pending.future.set_result(
                    {
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "session terminated",
                    }
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

    def terminal_snapshot(self, session_id: str) -> str:
        state = self._sessions.get(session_id)
        if state is None:
            return ""
        return "".join(state.terminal_fragments)

    async def _spawn(
        self,
        session_id: str,
        cwd: str,
        claude_session_id: str,
        resume: bool,
        launch_factory_override: LaunchFactory | None = None,
        permission_mode: str | None = None,
    ) -> ClaudeSessionState:
        launch_factory = launch_factory_override or self._launch_factory
        if launch_factory is None:
            spec = self._build_local_launch_spec(
                session_id, cwd, claude_session_id, resume
            )
        else:
            spec = launch_factory(session_id, cwd, claude_session_id, resume)
        process = await asyncio.create_subprocess_exec(
            *spec.args,
            cwd=spec.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=spec.env,
        )
        state = ClaudeSessionState(
            session_id=session_id,
            cwd=cwd,
            process=process,
            claude_session_id=claude_session_id,
            stdout_task=asyncio.create_task(asyncio.sleep(0)),  # placeholder
            stderr_task=asyncio.create_task(asyncio.sleep(0)),
            wait_task=asyncio.create_task(asyncio.sleep(0)),
            permission_mode=(
                permission_mode
                if permission_mode in CLAUDE_PERMISSION_MODES
                else "default"
            ),
        )
        state.stdout_task = asyncio.create_task(self._read_stdout(state))
        state.stderr_task = asyncio.create_task(self._read_stderr(state))
        state.wait_task = asyncio.create_task(self._watch_process(state))
        self._sessions[session_id] = state
        return state

    def _build_local_launch_spec(
        self, session_id: str, cwd: str, claude_session_id: str, resume: bool
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
            "--settings",
            str(self._hook_settings_path),
            "--permission-mode",
            self._permission_mode,
        ]
        if resume:
            args.extend(["--resume", claude_session_id])
        else:
            args.extend(["--session-id", claude_session_id])
        env = {
            **os.environ,
            "WAYPOINT_HOOK_URL": self._hook_url,
            "WAYPOINT_HOOK_SECRET": self._hook_secret,
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
                line = await state.process.stdout.readline()
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
                line = await state.process.stderr.readline()
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
                self._format_rate_limit(event.get("rate_limit_info", {})),
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
            await self._emit_event(
                state.session_id,
                EventKind.SYSTEM_NOTE,
                f"Claude session ready (model {event.get('model', 'unknown')})",
                {
                    "method": "system.init",
                    "payload": event,
                    "status": SessionStatus.IDLE,
                },
                SessionStatus.IDLE,
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
            text, status = self._format_status_event(event)
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
                self._format_compact_boundary(metadata),
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
        for block in message.get("content") or []:
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
                    # noise.
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

    async def _handle_user(
        self, state: ClaudeSessionState, event: dict[str, Any]
    ) -> None:
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if block.get("type") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id") or "")
            content = block.get("content")
            text = self._stringify_tool_result(content)
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
                state.terminal_fragments.append(text + "\n")
            await self._emit_event(state.session_id, kind, text, metadata, status)

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

    def _format_status_event(self, event: dict[str, Any]) -> tuple[str, SessionStatus]:
        status_label = event.get("status")
        compact_result = event.get("compact_result")
        if status_label == "compacting":
            return "Compacting context…", SessionStatus.RUNNING
        if compact_result is not None:
            return (
                f"Context compaction {compact_result}",
                (
                    SessionStatus.IDLE
                    if compact_result == "success"
                    else SessionStatus.ERROR
                ),
            )
        return "", SessionStatus.RUNNING

    def _format_compact_boundary(self, metadata: dict[str, Any]) -> str:
        pre = metadata.get("pre_tokens")
        post = metadata.get("post_tokens")
        duration_ms = metadata.get("duration_ms")
        trigger = metadata.get("trigger") or "manual"
        parts = [f"Context compacted ({trigger})"]
        if pre is not None and post is not None:
            parts.append(f"{pre} → {post} tokens")
        if duration_ms is not None:
            parts.append(f"{duration_ms} ms")
        return " · ".join(parts)

    def _format_rate_limit(self, info: dict[str, Any]) -> str:
        status = info.get("status", "unknown")
        rl_type = info.get("rate_limit_type", "")
        return f"Rate limit ({rl_type}): {status}".strip()

    def _format_approval_text(self, payload: dict[str, Any]) -> str:
        tool_name = payload.get("tool_name") or "tool"
        tool_input = payload.get("tool_input") or {}
        if tool_name == "Bash":
            command = tool_input.get("command") or ""
            return f"Approve Bash command:\n{command}"
        if tool_name in {"Edit", "Write", "MultiEdit"}:
            path = tool_input.get("file_path") or tool_input.get("path") or ""
            return f"Approve {tool_name} on {path}"
        if tool_name == "ExitPlanMode":
            # Plan text is already rendered as a markdown agent_output above
            # this card — keep this prompt compact to avoid duplication.
            return "Approve plan and exit plan mode"
        return f"Approve {tool_name}: {json.dumps(tool_input)[:240]}"

    def _stringify_tool_result(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for entry in content:
                if isinstance(entry, dict):
                    if entry.get("type") == "text" and isinstance(
                        entry.get("text"), str
                    ):
                        parts.append(entry["text"])
                    elif "text" in entry:
                        parts.append(str(entry["text"]))
                    else:
                        parts.append(json.dumps(entry))
                else:
                    parts.append(str(entry))
            return "\n".join(parts)
        return json.dumps(content)

    def _map_decision(self, decision: str) -> str:
        lowered = decision.strip().lower()
        if lowered in {"approve", "accept", "yes", "y", "allow", "acceptforsession"}:
            return "allow"
        return "deny"

    def _require_session(self, session_id: str) -> ClaudeSessionState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise ClaudeCliError(f"claude session not active: {session_id}") from exc
