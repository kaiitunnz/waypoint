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

from waypoint.backends.claude_code.normalize import (
    format_approval_text,
    format_compact_boundary,
    format_rate_limit,
    format_status_event,
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
from waypoint.schemas import EventKind, SessionStatus

log = logging.getLogger("waypoint.claude_cli")

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
    if mode == "plan" and tool_name == "Write" and tool_input is not None:
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
def _is_plan_file_path(path: str) -> bool:
    if not path:
        return False
    return "/.claude/plans/" in path


# Maps a Waypoint per-session mode to the value the Claude CLI accepts on
# `--permission-mode`. `auto` and `dontAsk` are Waypoint-side hook
# short-circuits with no Claude equivalent — we launch Claude in `default`
# and let the hook auto-allow gated tools.
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
LaunchFactory = Callable[
    [str, str, str, bool, str, str | None, str | None],
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
    # AskUserQuestion has shouldDefer:true + requiresUserInteraction:true.
    # In `-p` mode without a hook to block it, the binary auto-rejects the
    # tool with "User declined to answer questions" before Waypoint can
    # surface the question to the user. Routing it through PreToolUse keeps
    # the binary parked until respond_to_ask_question resolves the future.
    "AskUserQuestion",
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
    # Mode the session was in immediately before transitioning into "plan".
    # ExitPlanMode approval restores this so users who were in (e.g.)
    # acceptEdits before opening a plan don't get bumped down to default.
    pre_plan_mode: str | None = None
    last_plan_path: str | None = None
    last_plan_content: str | None = None
    closing: bool = False
    model: str | None = None
    # Reasoning effort. Claude's CLI accepts `--effort <level>` at launch
    # only — there is no in-process control_request to swap it — so changing
    # this value at runtime requires terminating and respawning the process
    # with `--resume <claude_session_id>` and the new flag.
    effort: str | None = None
    # Captures the launch_factory used to spawn this session so set_effort
    # can respawn through the same factory (local vs. remote SSH) without
    # re-resolving target config.
    launch_factory: LaunchFactory | None = None


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
        launch_factory: LaunchFactory | None = None,
    ) -> None:
        self._emit_event = emit_event
        self._hook_settings_path = hook_settings_path
        self._hook_secret = hook_secret
        self._hook_url = hook_url
        self._binary = binary
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
        model: str | None = None,
        effort: str | None = None,
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
        # without consulting the database on every PreToolUse hit. When
        # transitioning into plan from any other mode, also record the
        # outgoing mode so ExitPlanMode approval can restore it instead of
        # always dropping to default.
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
        IDs (``claude-opus-4-7``); append ``[1m]`` for 1M-context variants.
        """
        request_id = f"set-model-{uuid.uuid4()}"
        payload: dict[str, Any] = {"subtype": "set_model"}
        if model:
            payload["model"] = model
        await self._send_control_request(session_id, request_id, payload)
        state = self._sessions.get(session_id)
        if state is not None:
            state.model = model or None

    def session_model(self, session_id: str) -> str | None:
        state = self._sessions.get(session_id)
        return state.model if state is not None else None

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
        launch_factory = previous.launch_factory
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
        """Resolve a PreToolUse hook waiting on AskUserQuestion.

        AskUserQuestion is gated through the same hook flow as approvals so
        the binary doesn't auto-decline. Once the user answers, we return
        `permissionDecision: deny` with the answer payload as the reason —
        that string becomes the tool_result Claude reads, matching the
        binary's own `User has answered your questions: …` shape.
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
        if not pending.future.done():
            pending.future.set_result(
                {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"User has answered your questions: {answer_text}. "
                        "You can now continue with the user's answers in mind."
                    ),
                }
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
        self, session_id: str, decision: str, text: str | None = None
    ) -> bool:
        state = self._sessions.get(session_id)
        if state is None or not state.pending:
            return False
        # Resolve oldest pending first.
        tool_use_id, pending = next(iter(state.pending.items()))
        mapped = self._map_decision(decision)
        tool_name = pending.payload.get("tool_name")
        # ExitPlanMode is special: in `-p` mode the binary's tool echoes the
        # dialog title ("Exit plan mode?") as the result, which Claude reads as
        # "dismissed" even when the user accepted. Block the tool with deny and
        # carry the verdict in the reason so Claude proceeds correctly. On
        # accept we also flip the binary out of plan mode via control_request,
        # mirroring what the TUI does after the dialog returns.
        if tool_name == "ExitPlanMode":
            response = await self._exit_plan_mode_response(
                state, mapped, pending.payload, text
            )
        else:
            reason = "approved by user" if mapped == "allow" else "denied by user"
            if text:
                reason = f"{reason}\n\nUser note:\n{text}"
            response = {
                "permissionDecision": mapped,
                "permissionDecisionReason": reason,
            }
        if not pending.future.done():
            pending.future.set_result(response)
        state.pending.pop(tool_use_id, None)
        return True

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
            tool_input = payload.get("tool_input")
            tool_input_dict = tool_input if isinstance(tool_input, dict) else None
            # AskUserQuestion always surfaces to the user — auto-approving any
            # mode would let the binary's defer path auto-decline before the
            # answer arrives.
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
                    and tool_name == "Write"
                    and tool_input_dict is not None
                ):
                    path = str(tool_input_dict.get("file_path") or "")
                    if _is_plan_file_path(path):
                        state.last_plan_path = path
                        content = tool_input_dict.get("content")
                        if isinstance(content, str):
                            state.last_plan_content = content
                return auto

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
                        plan_text = Path(state.last_plan_path).read_text(
                            encoding="utf-8"
                        )
                        tool_input_dict["plan"] = plan_text
                    except Exception as exc:
                        log.warning(
                            "failed to read plan file for ExitPlanMode approval card",
                            extra={"path": state.last_plan_path, "error": str(exc)},
                        )

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
                # AskUserQuestion's tool_call event already renders the
                # question UI in the transcript via parseAskUserQuestion;
                # emitting a separate APPROVAL_REQUEST card would show the
                # same prompt twice. Only register the future so
                # respond_to_ask_question can resolve it.
                if tool_name != "AskUserQuestion":
                    await self._emit_event(
                        waypoint_session_id,
                        EventKind.APPROVAL_REQUEST,
                        format_approval_text(payload),
                        {
                            "tool_name": payload.get("tool_name"),
                            "tool_input": payload.get("tool_input"),
                            "tool_use_id": tool_use_id,
                            "method": "PreToolUse",
                            "status": SessionStatus.WAITING_INPUT,
                        },
                        SessionStatus.WAITING_INPUT,
                    )
        timeout = 3600.0 if tool_name == "AskUserQuestion" else DEFAULT_TIMEOUT_SECONDS
        try:
            decision = await asyncio.wait_for(pending.future, timeout=timeout)
        except TimeoutError:
            decision = {
                "permissionDecision": "deny",
                "permissionDecisionReason": "approval timed out",
            }
            state.pending.pop(tool_use_id, None)

            # Emit a system note so the frontend knows to dequeue the approval card
            await self._emit_event(
                waypoint_session_id,
                EventKind.SYSTEM_NOTE,
                "Approval timed out",
                {"status": SessionStatus.RUNNING},
                SessionStatus.RUNNING,
            )
        return decision

    def has_pending_approval(self, session_id: str) -> bool:
        state = self._sessions.get(session_id)
        return bool(state and state.pending)

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
        model: str | None = None,
        effort: str | None = None,
    ) -> ClaudeSessionState:
        resolved_mode = (
            permission_mode if permission_mode in CLAUDE_PERMISSION_MODES else "default"
        )
        cli_mode = claude_cli_mode_for(resolved_mode)
        launch_factory = launch_factory_override or self._launch_factory
        if launch_factory is None:
            spec = self._build_local_launch_spec(
                session_id, cwd, claude_session_id, resume, cli_mode, model, effort
            )
        else:
            spec = launch_factory(
                session_id, cwd, claude_session_id, resume, cli_mode, model, effort
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
            model=model,
            effort=effort or None,
            launch_factory=launch_factory,
        )
        state.stdout_task = asyncio.create_task(self._read_stdout(state))
        state.stderr_task = asyncio.create_task(self._read_stderr(state))
        state.wait_task = asyncio.create_task(self._watch_process(state))
        self._sessions[session_id] = state
        return state

    def _build_local_launch_spec(
        self,
        session_id: str,
        cwd: str,
        claude_session_id: str,
        resume: bool,
        cli_mode: str,
        model: str | None = None,
        effort: str | None = None,
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
            cli_mode,
        ]
        if model:
            args.extend(["--model", model])
        if effort:
            args.extend(["--effort", effort])
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
                try:
                    line = await state.process.stdout.readline()
                except asyncio.LimitOverrunError as exc:
                    # Line exceeded the StreamReader buffer. Drain and skip it
                    # so the reader survives; the lost line is almost always a
                    # giant tool-result blob we couldn't have parsed anyway.
                    await state.process.stdout.readexactly(exc.consumed)
                    await _drain_until_newline(state.process.stdout)
                    log.warning(
                        "claude stdout line exceeded buffer limit; dropped",
                        extra={
                            "session_id": state.session_id,
                            "consumed_bytes": exc.consumed,
                        },
                    )
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
                try:
                    line = await state.process.stderr.readline()
                except asyncio.LimitOverrunError as exc:
                    await state.process.stderr.readexactly(exc.consumed)
                    await _drain_until_newline(state.process.stderr)
                    log.warning(
                        "claude stderr line exceeded buffer limit; dropped",
                        extra={
                            "session_id": state.session_id,
                            "consumed_bytes": exc.consumed,
                        },
                    )
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
        for block in iter_content_blocks(message.get("content")):
            if block.get("type") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id") or "")
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
