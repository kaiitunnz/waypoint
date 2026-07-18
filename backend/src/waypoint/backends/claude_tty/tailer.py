"""Transcript file tailer for the claude_tty (Emulated) transport.

Polls the session JSONL transcript by byte offset, normalizes each new record
into canonical events, and emits them through the runtime.  Periodically checks
whether the tmux pane is still alive and marks the session EXITED when it dies.

Also polls the live pane for tool-permission dialogs (when the session's
permission_mode requires them) and emits APPROVAL_REQUEST events so the
frontend can surface them.
"""

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from waypoint.backends.claude_code.adapter import (
    _context_usage_snapshot_from_message,
    claude_token_usage_record,
)
from waypoint.backends.claude_code.normalize import format_approval_text
from waypoint.backends.claude_tty import pane_dialog
from waypoint.backends.claude_tty._state import PendingTtyApproval, PendingTtyQuestion
from waypoint.backends.claude_tty.byte_source import (
    TranscriptByteSource,
    transcript_path,
)
from waypoint.backends.claude_tty.normalize import TranscriptNormalizer
from waypoint.backends.events import (
    INTERACTION_METADATA_KEY,
    InteractionChoice,
    InteractionEnvelope,
)
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.schemas import EventKind, SessionRecord, SessionStatus

if TYPE_CHECKING:
    from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_tty")

# ``transcript_path`` moved to ``byte_source`` (where the local source uses it);
# re-exported here for callers/tests that still import it from the tailer.
__all__ = ["TranscriptTailer", "transcript_path"]

_POLL_INTERVAL = 0.5  # seconds between transcript polls
_PANE_CHECK_INTERVAL = 10.0  # seconds between tmux pane liveness checks
_DIALOG_POLL_INTERVAL = 1.0  # seconds between live-pane dialog captures
_DIALOG_STABLE_TICKS = 2  # consecutive identical captures before surfacing
# Protective cap on the unparsed trailing buffer: a JSONL record that never
# completes past this size is dropped rather than grown without bound.
_MAX_PARTIAL_BYTES = 8 * 1024 * 1024


class TranscriptTailer:
    """Background task that tails a Claude TUI transcript and emits events.

    ``start_at_end=True`` skips existing content (used on boot-time restore so
    already-emitted records are not replayed).  The default (``False``) reads
    from byte 0, which is correct for fresh sessions where the transcript file
    will be created on first user input.
    """

    def __init__(
        self,
        session_id: str,
        source: TranscriptByteSource,
        runtime: "SessionRuntime",
        plugin: "ClaudeTtyPlugin",
        *,
        start_at_end: bool = False,
        config_dir: str | None = None,
    ) -> None:
        self._session_id = session_id
        self._source = source
        self._runtime = runtime
        self._plugin = plugin
        self._normalizer = TranscriptNormalizer(config_dir)
        self._pane_check_elapsed = 0.0
        self._dialog_check_elapsed = 0.0
        # Cursor state, source-independent. ``start_at_end`` is applied lazily on
        # the first successful observation (see ``_drain``), never here — the
        # constructor runs on the event loop and must not do IO.
        self._start_at_end = start_at_end
        self._primed = False
        self._fetch_offset = 0
        self._partial = b""
        self._identity: tuple[int, int] | None = None
        self._context_usage_signature: (
            tuple[int, int | None, tuple[tuple[str, int], ...]] | None
        ) = None
        # Dialog debounce state
        self._prev_dialog_sig: str | None = None
        self._dialog_stable_count: int = 0
        # Signature of the dialog already surfaced as an APPROVAL_REQUEST; held
        # until the dialog leaves the screen so a response that clears pending
        # before the pane redraws cannot trigger a duplicate emit.
        self._surfaced_sig: str | None = None
        # True once we have Esc-dismissed the current AskUserQuestion popup, so
        # the dismissal fires once per appearance; reset when the pane leaves
        # the question screen.
        self._question_dismissed: bool = False

    async def _drain(self, *, force: bool = False) -> None:
        # The priming tick fetches size + identity only (no body) so start-at-end
        # never downloads history; ``force`` bypasses a remote source's poll
        # cadence (terminal drain on exit).
        metadata_only = self._start_at_end and not self._primed
        read = await asyncio.to_thread(
            self._source.read_from,
            self._fetch_offset,
            metadata_only=metadata_only,
            force=force,
        )
        if not read.observed:
            return

        # First observation: record identity and, for start-at-end, jump to the
        # current end without parsing (records already on disk are in the DB).
        # This is correct regardless of whether the source resolved at
        # construction time, so a transient boot-time miss never replays history.
        if not self._primed:
            self._primed = True
            self._identity = read.identity
            if self._start_at_end:
                self._fetch_offset = read.size or 0
                self._partial = b""
                return

        # Truncation (size shrank below the cursor) or replacement (file identity
        # changed): the store cannot dedup a replay, so skip the ambiguous bytes,
        # jump to the new end, and record one content-free note. Never re-read.
        identity_changed = (
            self._identity is not None
            and read.identity is not None
            and read.identity != self._identity
        )
        truncated = read.size is not None and read.size < self._fetch_offset
        if identity_changed or truncated:
            self._partial = b""
            self._fetch_offset = read.size or 0
            self._identity = read.identity
            log.warning(
                "transcript discontinuity; skipping to end",
                extra={
                    "session_id": self._session_id,
                    "reason": "identity_changed" if identity_changed else "truncated",
                },
            )
            await self._runtime._record_system_event(
                self._session_id,
                "Transcript was replaced or truncated; resuming from its end.",
            )
            return

        self._identity = read.identity or self._identity
        if not read.data:
            return

        buf = self._partial + read.data
        self._fetch_offset += len(read.data)
        parts = buf.split(b"\n")
        # A trailing element without a newline is an incomplete record; keep it
        # for the next read. ``split`` always yields a final element (b"" when
        # ``buf`` ends in a newline), so this uniformly handles both cases.
        self._partial = parts[-1]
        lines = parts[:-1]
        if len(self._partial) > _MAX_PARTIAL_BYTES:
            log.warning(
                "transcript partial record exceeded cap; dropping",
                extra={"session_id": self._session_id},
            )
            self._partial = b""

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                log.warning(
                    "transcript JSON decode error",
                    extra={"session_id": self._session_id, "line": raw_line[:200]},
                )
                continue
            for ev in self._normalizer.process_record(record):
                if (
                    ev.kind == EventKind.TOOL_CALL
                    and ev.metadata.get("tool_name") == "AskUserQuestion"
                    and ev.status == SessionStatus.WAITING_INPUT
                ):
                    tool_use_id = str(ev.metadata.get("tool_use_id") or "")
                    self._plugin._pending_questions[self._session_id] = (
                        PendingTtyQuestion(
                            approval_id=uuid.uuid4().hex,
                            tool_use_id=tool_use_id,
                        )
                    )
                await self._runtime._emit_adapter_event(
                    self._session_id,
                    ev.kind,
                    ev.text,
                    ev.metadata,
                    ev.status,
                )
            if record.get("type") == "assistant":
                await self._maybe_publish_context_usage(record)

    async def _maybe_publish_context_usage(self, record: dict[str, Any]) -> None:
        message: dict[str, Any] = record.get("message") or {}
        usage: dict[str, Any] = message.get("usage") or {}
        # Prefer the session's configured model alias for the window: it carries
        # the ``[1m]`` marker (→ 1M window), whereas the transcript's resolved API
        # id normalizes to the base family and loses it. Read it fresh each publish
        # so a dynamic model change is reflected on the next snapshot.
        session = self._runtime.storage.get_session(self._session_id)
        model = (session.model if session is not None else None) or (
            str(message.get("model") or "") or None
        )
        snapshot = _context_usage_snapshot_from_message(model, usage)
        if snapshot is None:
            return
        # The ledger's model is the concrete resolved id for *this* turn, taken
        # from the transcript message itself (authoritative "actual model at
        # turn time", FR-4) — never the session's mutable latest ``resolved_model``,
        # which would rewrite earlier turns onto the current model when the
        # transcript is replayed from offset 0 after a resume. The session model
        # is only a fallback for a message with no model field.
        record_model = (str(message.get("model") or "") or None) or (
            session.resolved_model if session is not None else None
        )
        effort = session.effort if session is not None else None
        # Key on the breakdown too, so a same-total/different-split turn refreshes.
        sig = (
            snapshot.used_tokens,
            snapshot.context_window_tokens,
            tuple(sorted(snapshot.breakdown.items())),
        )
        context_changed = sig != self._context_usage_signature
        # Key the ledger on the message id (uuid fallback) so offset-zero replay
        # is idempotent; recorded regardless of the snapshot dedup so two turns
        # with identical totals both count. Broadcast it here only when the
        # context publish below won't (a deduped snapshot), so the aggregate
        # increment is never stranded, yet a changed turn still emits one frame.
        record_id = str(message.get("id") or record.get("uuid") or "")
        token_record = claude_token_usage_record(
            record_id, snapshot, model=record_model, effort=effort
        )
        if token_record is not None:
            await self._runtime.publish_token_usage_record(
                self._session_id, token_record, publish=not context_changed
            )
        if not context_changed:
            return
        self._context_usage_signature = sig
        await self._runtime.update_session_fields(
            self._session_id, context_usage=snapshot
        )

    async def _poll_dialog(self) -> None:
        """Capture the live pane and surface any stable tool-permission dialog.

        Detection is intentionally mode-agnostic: ``claude_tty`` only changes
        permission mode by relaunching the pane, so the stored
        ``permission_mode`` holds whatever the last launch passed, while the
        TUI's real posture can drift independently (a human pressing shift+tab
        in the pane, or choosing "allow all this session"). Gating on the
        stored mode would miss a dialog that appears after an auto→prompting
        drift and hang the session, so we always trust the on-screen dialog
        rather than the recorded mode. (The run loop throttles how often this
        runs to keep the capture cost low on sessions that never prompt.)
        """
        session = self._runtime.storage.get_session(self._session_id)
        if session is None:
            return

        state = session.transport_state
        pane = state.get("tmux_pane") or state.get("tmux_session") or self._session_id

        try:
            snapshot = await self._runtime.tmux.capture_snapshot(pane)
        except TmuxError:
            return

        screen_type = pane_dialog.classify(snapshot)

        if screen_type is pane_dialog.PaneScreen.TRUST:
            # A fresh cwd opens with the workspace-trust prompt, which blocks the
            # session (including autonomously-spawned ones in fresh worktrees)
            # until answered. Option 1 ("trust") is preselected, so a bare Enter
            # accepts. Re-sent each tick it persists — idempotent, and self-heals
            # if a keystroke is dropped; a stray Enter at the ready prompt after
            # it clears is a harmless empty submit.
            log.info(
                "accepting workspace-trust prompt",
                extra={"session_id": self._session_id},
            )
            await self._runtime.tmux.send_input(pane, "", submit=True)
            return

        if screen_type is pane_dialog.PaneScreen.QUESTION:
            # The AskUserQuestion popup withholds its structured questions from
            # the transcript until it is resolved, so it is invisible to the
            # tailer while it blocks the turn. Esc dismisses it, which flushes
            # the full tool_use record to the JSONL; the armed normalizer then
            # surfaces it as an answerable card (and swallows the resulting
            # "user rejected" result). The answer is delivered later as a normal
            # user turn via the plugin's answer_question.
            if self._prev_dialog_sig == "question":
                self._dialog_stable_count += 1
            else:
                self._prev_dialog_sig = "question"
                self._dialog_stable_count = 1
            if (
                self._dialog_stable_count >= _DIALOG_STABLE_TICKS
                and not self._question_dismissed
            ):
                log.info(
                    "dismissing AskUserQuestion popup to surface it",
                    extra={"session_id": self._session_id},
                )
                await self._runtime.tmux.send_bytes(pane, b"\x1b")
                self._normalizer.arm_question_dismissal()
                self._question_dismissed = True
            return

        if screen_type is pane_dialog.PaneScreen.PLAN:
            await self._surface_plan_dialog(session, snapshot)
            return

        if screen_type is not pane_dialog.PaneScreen.APPROVAL:
            # Dialog gone — clear any pending approval for this session.
            self._plugin._pending_approvals.pop(self._session_id, None)
            self._prev_dialog_sig = None
            self._dialog_stable_count = 0
            self._surfaced_sig = None
            self._question_dismissed = False
            return

        dialog = pane_dialog.parse_approval(snapshot)
        if dialog is None:
            self._prev_dialog_sig = None
            self._dialog_stable_count = 0
            return

        sig = f"{dialog.tool_name}:{dialog.target}:{dialog.question}"

        if sig == self._prev_dialog_sig:
            self._dialog_stable_count += 1
        else:
            self._prev_dialog_sig = sig
            self._dialog_stable_count = 1

        if self._dialog_stable_count < _DIALOG_STABLE_TICKS:
            return

        # Already surfaced this exact dialog — do not re-emit, even once the
        # response has cleared the pending entry but the pane has not yet
        # redrawn the box away.
        if sig == self._surfaced_sig:
            return

        approve_num = dialog.approve_option.number if dialog.approve_option else 1
        decline_opt = dialog.decline_option
        decline_num = decline_opt.number if decline_opt else None

        approval_id = str(uuid.uuid4())
        tool_name = dialog.tool_name or "Unknown"
        target = dialog.target or ""

        if tool_name == "Bash":
            tool_input: dict[str, str] = {"command": target}
        elif tool_name in {"Write", "Edit", "MultiEdit"}:
            tool_input = {"file_path": target}
        else:
            tool_input = {"description": target}

        payload: dict[str, Any] = {"tool_name": tool_name, "tool_input": tool_input}
        text = format_approval_text(payload)

        self._plugin._pending_approvals[self._session_id] = PendingTtyApproval(
            approval_id=approval_id,
            tool_name=tool_name,
            target=target or None,
            approve_number=approve_num,
            decline_number=decline_num,
            signature=sig,
        )
        self._surfaced_sig = sig

        interaction = InteractionEnvelope(
            kind="approval",
            request_id=approval_id,
            title=f"Approve {tool_name}",
            body=text,
            choices=[
                InteractionChoice(label="approve"),
                InteractionChoice(label="decline"),
            ],
        )
        await self._runtime._emit_adapter_event(
            self._session_id,
            EventKind.APPROVAL_REQUEST,
            text,
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "approval_id": approval_id,
                "method": "tty_permission",
                "status": SessionStatus.WAITING_INPUT,
                INTERACTION_METADATA_KEY: interaction.to_metadata(),
            },
            SessionStatus.WAITING_INPUT,
        )

    async def _surface_plan_dialog(self, session: SessionRecord, snapshot: str) -> None:
        """Surface the ExitPlanMode dialog as the same approval card Chat shows.

        The dialog is the plan-mode analogue of a tool-permission prompt: the
        binary withholds the ExitPlanMode tool_use from the transcript while the
        dialog blocks, so it is read off the pane. The plan body comes from the
        plan-file Write the normalizer already captured, matching the Chat card's
        ``tool_input.plan``. Decline falls through to Esc (``decline_number=None``),
        which keeps plan mode.

        Approve presses the option that restores the pre-plan mode, the way Chat
        does — but only as far as the dialog can express it: the auto option exits
        to ``auto`` and the manual option to ``default``. A pre-plan ``auto`` is
        restored when that option is present; everything else (including
        ``acceptEdits``/``bypassPermissions``/``dontAsk``, which no option maps to,
        and a launched-in-plan session with no recorded prior mode) falls back to
        the manual option → ``default``, which never widens permissions.
        """
        dialog = pane_dialog.parse_plan_dialog(snapshot)
        manual_option = dialog.manual_option if dialog is not None else None
        if dialog is None or manual_option is None:
            self._prev_dialog_sig = None
            self._dialog_stable_count = 0
            return

        sig = f"ExitPlanMode:{dialog.plan_path}"
        if sig == self._prev_dialog_sig:
            self._dialog_stable_count += 1
        else:
            self._prev_dialog_sig = sig
            self._dialog_stable_count = 1
        if self._dialog_stable_count < _DIALOG_STABLE_TICKS:
            return
        if sig == self._surfaced_sig:
            return

        pre_plan = session.transport_state.get("pre_plan_mode")
        target_mode = pre_plan if isinstance(pre_plan, str) and pre_plan else "default"
        if target_mode == "auto" and dialog.auto_option is not None:
            approve_option, restore_mode = dialog.auto_option, "auto"
        else:
            approve_option, restore_mode = manual_option, "default"

        plan_path = self._normalizer.last_plan_path or dialog.plan_path
        tool_input: dict[str, Any] = {"plan": self._normalizer.last_plan_content or ""}
        if plan_path:
            tool_input["planFilePath"] = plan_path

        approval_id = str(uuid.uuid4())
        self._plugin._pending_approvals[self._session_id] = PendingTtyApproval(
            approval_id=approval_id,
            tool_name="ExitPlanMode",
            target=plan_path,
            approve_number=approve_option.number,
            decline_number=None,
            signature=sig,
            is_plan=True,
            restore_mode=restore_mode,
        )
        self._surfaced_sig = sig

        payload = {"tool_name": "ExitPlanMode", "tool_input": tool_input}
        interaction = InteractionEnvelope(
            kind="plan_approval",
            request_id=approval_id,
            title="Approve plan",
            body=tool_input.get("plan") or None,
            plan_item_id=approval_id,
            choices=[
                InteractionChoice(label="approve"),
                InteractionChoice(label="decline"),
            ],
        )
        await self._runtime._emit_adapter_event(
            self._session_id,
            EventKind.APPROVAL_REQUEST,
            format_approval_text(payload),
            {
                "tool_name": "ExitPlanMode",
                "tool_input": tool_input,
                "approval_id": approval_id,
                "method": "tty_permission",
                "status": SessionStatus.WAITING_INPUT,
                INTERACTION_METADATA_KEY: interaction.to_metadata(),
            },
            SessionStatus.WAITING_INPUT,
        )

    async def _pane_alive(self) -> bool:
        session = self._runtime.storage.get_session(self._session_id)
        if session is None:
            return False
        state = session.transport_state
        target = state.get("tmux_pane") or state.get("tmux_session") or self._session_id
        try:
            info = await self._runtime.tmux.describe_target(target)
        except TmuxError:
            return False
        return not info.pane_dead

    async def run(self) -> None:
        try:
            while True:
                session = self._runtime.storage.get_session(self._session_id)
                if session is None:
                    return

                await self._drain()

                if session.status not in (SessionStatus.EXITED, SessionStatus.ERROR):
                    self._dialog_check_elapsed += _POLL_INTERVAL
                    if self._dialog_check_elapsed >= _DIALOG_POLL_INTERVAL:
                        self._dialog_check_elapsed = 0.0
                        await self._poll_dialog()

                if session.status in (SessionStatus.EXITED, SessionStatus.ERROR):
                    # One final drain in case records landed between the status
                    # check and this point. ``force`` bypasses a remote source's
                    # poll cadence so a tail written in the last second isn't lost.
                    await self._drain(force=True)
                    return

                self._pane_check_elapsed += _POLL_INTERVAL
                if self._pane_check_elapsed >= _PANE_CHECK_INTERVAL:
                    self._pane_check_elapsed = 0.0
                    if not await self._pane_alive():
                        await self._drain(force=True)
                        await self._runtime._record_system_event(
                            self._session_id,
                            "Claude TUI session exited",
                            status=SessionStatus.EXITED,
                        )
                        return

                await asyncio.sleep(_POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "transcript tailer crashed",
                extra={"session_id": self._session_id},
            )
