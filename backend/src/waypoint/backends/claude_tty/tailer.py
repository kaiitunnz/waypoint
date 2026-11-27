"""Transcript file tailer for the claude_tty (Emulated) transport.

Polls the session JSONL transcript by byte offset, normalizes each new record
into canonical events, and emits them through the runtime.  Periodically checks
whether the tmux pane is still alive and marks the session EXITED when it dies.

Also polls the live pane for tool-permission dialogs and emits APPROVAL_REQUEST
events so the frontend can surface them. The pane only shows a dialog; the call
it asks about is looked up in the transcripts (``pending_calls``) and named when
``call_match`` is certain of it. Such an ask has ``call_verified`` true and the
call's full input; any other carries the pane's reading.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from waypoint.backends.approvals import open_approval_requests
from waypoint.backends.claude_code.adapter import (
    _context_usage_snapshot_from_message,
    claude_token_usage_record,
)
from waypoint.backends.claude_code.models import (
    ClaudeContextWindowResolver,
    claude_context_window_for_model,
    is_claude_plan_switching_model,
    observe_claude_model,
)
from waypoint.backends.claude_code.normalize import format_approval_text
from waypoint.backends.claude_code.session_folder import follow_relocation
from waypoint.backends.claude_tty import pane_dialog
from waypoint.backends.claude_tty._state import PendingTtyApproval
from waypoint.backends.claude_tty.byte_source import READ_LIMIT, TailedTranscript
from waypoint.backends.claude_tty.call_match import match_call, match_plan_call
from waypoint.backends.claude_tty.normalize import (
    SYNTHETIC_MODEL_ID,
    TranscriptNormalizer,
)
from waypoint.backends.claude_tty.pending_calls import (
    CallSource,
    PendingCall,
    TranscriptCalls,
)
from waypoint.backends.events import (
    INTERACTION_METADATA_KEY,
    InteractionChoice,
    InteractionEnvelope,
    InteractionKind,
)
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.schemas import EventKind, SessionRecord, SessionStatus

if TYPE_CHECKING:
    from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_tty")

_POLL_INTERVAL = 0.5  # seconds between transcript polls
_PANE_CHECK_INTERVAL = 10.0  # seconds between tmux pane liveness checks
_DIALOG_POLL_INTERVAL = 1.0  # seconds between live-pane dialog captures
_DIALOG_STABLE_TICKS = 2  # consecutive identical captures before surfacing
# Dialog polls an unverified card is matched again for a late-written call.
_REMATCH_POLLS = 5
# Dialog polls between checks that a verified card's call is still pending.
_RESOLVED_CHECK_POLLS = 5
# Polls a replied-to dialog is given to redraw, and after which one still drawn
# is raised again even without another call to name.
_REDRAW_POLLS = 2
_LOST_REPLY_POLLS = 5
# Scrollback captured when a tall dialog's heading is above the usual window.
_DEEP_CAPTURE_LINE = -2000
_PLAN_LIMIT = 1024 * 1024
_ARROW_DOWN = b"\x1b[B"
_ARROW_UP = b"\x1b[A"
# Polls an unchanged trust prompt waits before re-sending a dropped arrow burst.
_TRUST_ARROW_RETRY_POLLS = 5
# Popups cancelled with one Escape after the debounce. The message is fixed —
# never interpolate pane contents, which can hold personal environment data.
_ESC_DISMISS_LOG: dict[pane_dialog.PaneScreen, str] = {
    pane_dialog.PaneScreen.QUESTION: "dismissing AskUserQuestion popup to surface it",
    pane_dialog.PaneScreen.AUTO_MODE_TEACHING: (
        "dismissing Claude auto-mode teaching popup"
    ),
}
_DIALOG_CLOSED_NOTE = "Approval dialog closed in the terminal"
# The dialog is still up, and what its card said about it has changed.
_UPDATED_NOTE = "Approval request updated"
_EXPIRED_NOTE = "Pending approval expired"
# Protective cap on the unparsed trailing buffer: a JSONL record that never
# completes past this size is dropped rather than grown without bound.
_MAX_PARTIAL_BYTES = 8 * 1024 * 1024
# The CLI re-appends ``relocated`` about every 32 KB it writes, so the tail of an
# existing transcript holds the latest one. One remote read covers it.
_RELOCATION_TAIL_BYTES = READ_LIMIT


@dataclass
class _Surfaced:
    """The dialog the current card was raised for."""

    identity: str
    signature: str
    approval_id: str
    call: PendingCall | None = None
    polls: int = 0
    polls_since_reply: int = 0


def _plan_signature(path: str | None, content: str | None) -> str:
    digest = (
        hashlib.sha256(content.encode()).hexdigest()[:16]
        if content is not None
        else "unread"
    )
    return f"plan:{path}:{digest}"


def _plan_approve_option(
    session: SessionRecord, dialog: pane_dialog.PlanDialog
) -> tuple[pane_dialog.DialogOption | None, str]:
    """The option approving a plan presses, and the mode it lands in.

    Approving restores the pre-plan mode, the way Chat does — but only as far as
    the dialog can express it: the auto option exits to ``auto`` and the manual
    option to ``default``. A pre-plan ``auto`` is restored when that option is
    present; everything else (including ``acceptEdits``/``bypassPermissions``/
    ``dontAsk``, which no option maps to, and a launched-in-plan session with no
    recorded prior mode) falls back to the manual option → ``default``, which
    never widens permissions.
    """
    pre_plan = session.transport_state.get("pre_plan_mode")
    if pre_plan == "auto" and dialog.auto_option is not None:
        return dialog.auto_option, "auto"
    return dialog.manual_option, "default"


def _pane_reading(dialog: pane_dialog.ApprovalDialog) -> tuple[str, dict[str, Any]]:
    """Tool name and input as the dialog reads, for an ask that names no call."""
    tool_name = dialog.tool_name or "Unknown"
    target = dialog.target or ""
    if tool_name == "Bash":
        return tool_name, {"command": target}
    if tool_name in {"Write", "Edit", "MultiEdit"}:
        return tool_name, {"file_path": target}
    return tool_name, {"description": target}


def _relocated_folder(record: dict[str, Any]) -> str | None:
    """The folder Claude says it moved the transcript to. The other records'
    ``cwd`` also follows a shell ``cd``, which moves nothing."""
    folder = record.get("relocatedCwd")
    if record.get("type") != "relocated" or not isinstance(folder, str):
        return None
    return folder or None


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
        source: TailedTranscript,
        runtime: "SessionRuntime",
        plugin: "ClaudeTtyPlugin",
        *,
        start_at_end: bool = False,
        calls: CallSource | None = None,
        context_window_resolver: ClaudeContextWindowResolver = (
            claude_context_window_for_model
        ),
    ) -> None:
        self._session_id = session_id
        self._source = source
        self._runtime = runtime
        self._plugin = plugin
        self._context_window_resolver = context_window_resolver
        self._calls = calls or TranscriptCalls(source)
        self._normalizer = TranscriptNormalizer(
            capture_enabled=runtime.settings.task_output_capture_enabled
        )
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
        # Previous reply's model for this pane, reset per construction so the
        # first reply after a boundary reads as first. Dedup for repeated records
        # of one message.
        self._last_observed_base: str | None = None
        self._model_seen_ids: set[str] = set()
        # Dialog debounce state
        self._prev_dialog_sig: str | None = None
        self._dialog_stable_count: int = 0
        # Trust-prompt navigation: the snapshot the last arrow burst acted on and
        # the polls since, and consecutive polls showing the trust option selected.
        self._trust_arrowed: tuple[str, int] | None = None
        self._trust_selected_polls: int = 0
        # The dialog already surfaced as an APPROVAL_REQUEST; held until it
        # leaves the screen so a response that clears pending before the pane
        # redraws cannot trigger a duplicate emit.
        self._surfaced: _Surfaced | None = None
        # Ids of calls that cannot be asking: replied to, or whose dialog left
        # the screen (answered in the terminal) two polls running.
        self._answered: set[str] = set()
        self._departing: PendingCall | None = None
        # Screen kind already Esc-dismissed this appearance. Keyed on the kind
        # (not a bool) so one popup replacing another does not suppress the next
        # Escape; reset when the pane leaves the screen.
        self._dismissed_screen: pane_dialog.PaneScreen | None = None
        # (dialog signature, approval id) of cards an earlier tailer left open:
        # a matching dialog on the pane adopts its card; the rest expire on the
        # first decided screen.
        self._adoptable: list[tuple[str | None, str]] = []
        self._stop_requested = False

    def stop(self) -> None:
        """Finish after one last forced drain, leaving dialog state for the next
        tailer to adopt."""
        self._stop_requested = True

    async def _drain(self, *, force: bool = False) -> None:
        # The priming tick fetches size + identity only (no body).
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
        # current end without emitting (records already on disk are in the DB).
        # This is correct regardless of whether the source resolved at
        # construction time, so a transient boot-time miss never replays history.
        if not self._primed:
            self._primed = True
            self._identity = read.identity
            if self._start_at_end:
                self._fetch_offset = read.size or 0
                self._partial = b""
                await self._follow_earlier_move(self._fetch_offset)
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

        relocated: str | None = None
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
            relocated = _relocated_folder(record) or relocated
            if record.get("type") == "assistant":
                # Before the normalizer, so the divider precedes the new-model turn.
                await self._maybe_observe_model(record)
            for ev in self._normalizer.process_record(record):
                if ev.kind is EventKind.STATUS_UPDATE and ev.status is not None:
                    await self._runtime.update_session_fields(
                        self._session_id, status=ev.status
                    )
                    continue
                await self._runtime._emit_adapter_event(
                    self._session_id,
                    ev.kind,
                    ev.text,
                    ev.metadata,
                    ev.status,
                )
            if record.get("type") == "assistant":
                await self._maybe_publish_context_usage(record)
        # After the records: the offset is past them, so a restart during the
        # follow would drop any not yet emitted.
        if relocated is not None:
            await follow_relocation(self._runtime, self._session_id, relocated)

    async def _follow_earlier_move(self, end: int) -> None:
        """Follow a move made before this tailer started, as after a restart or
        reattach."""
        read = await asyncio.to_thread(
            self._source.read_from, max(0, end - _RELOCATION_TAIL_BYTES), force=True
        )
        for line in reversed(read.data.split(b"\n")):
            if b'"relocated"' not in line:
                continue
            try:
                record: dict[str, Any] = json.loads(line)
            except ValueError:  # a window cut inside a character or record
                continue
            if folder := _relocated_folder(record):
                await follow_relocation(self._runtime, self._session_id, folder)
                return

    async def _maybe_observe_model(self, record: dict[str, Any]) -> None:
        """Record a real assistant reply's model, adopt it into the selection
        when diverged (no restart), and emit a ``model.change`` note. Synthetic,
        repeated same-id, and plan-switching selections are skipped.
        """
        message: dict[str, Any] = record.get("message") or {}
        concrete = str(message.get("model") or "")
        if not concrete or concrete == SYNTHETIC_MODEL_ID:
            return
        message_id = str(message.get("id") or record.get("uuid") or "")
        if message_id:
            if message_id in self._model_seen_ids:
                return
            self._model_seen_ids.add(message_id)

        session = self._runtime.storage.get_session(self._session_id)
        if session is None:
            return
        selection = session.model
        if is_claude_plan_switching_model(selection):
            return

        previous_base = self._last_observed_base
        observation = observe_claude_model(concrete, selection, previous_base)
        if observation is None:
            return
        self._last_observed_base = observation.resolved_base

        updates: dict[str, Any] = {"resolved_model": observation.resolved_base}
        if observation.adopt_selection is not None:
            updates["model"] = observation.adopt_selection
        if session.resolved_model != observation.resolved_base or "model" in updates:
            await self._runtime.update_session_fields(self._session_id, **updates)

        if observation.reason is None:
            return
        current = observation.resolved_base
        if observation.reason == "initial_mismatch":
            text = f"Running {current} (selected {selection})"
        else:
            text = f"Model changed to {current}"
        # session.status keeps the event-insert COALESCE a no-op; this note fires
        # mid-turn and must not flip the lifecycle.
        await self._runtime._emit_adapter_event(
            self._session_id,
            EventKind.SYSTEM_NOTE,
            text,
            {
                "method": "model.change",
                "reason": observation.reason,
                "previous_model": previous_base,
                "current_model": current,
                "selection": selection,
            },
            session.status,
        )

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
        snapshot = _context_usage_snapshot_from_message(
            model, usage, self._context_window_resolver
        )
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
        pending = self._plugin._pending_approvals.get(self._session_id)
        if pending is not None and pending.replying:
            return  # our own keystroke is moving the pane

        pane = self._pane(session)
        try:
            snapshot = await self._runtime.tmux.capture_snapshot(pane)
        except TmuxError:
            return

        screen_type = pane_dialog.classify(snapshot)

        if screen_type is pane_dialog.PaneScreen.TRUST:
            # A fresh cwd opens at the workspace-trust prompt, which blocks the
            # session until answered. Enter on "No, exit" ends the session, so
            # arrow onto the trust option first. The options wrap, so arrows wait
            # for a redraw (or a dropped-burst timeout) and Enter waits for the
            # selection to hold; a stray Enter after the prompt clears is a
            # harmless empty submit.
            offset = pane_dialog.trust_option_offset(snapshot)
            if offset is None:
                log.warning(
                    "workspace-trust prompt has no trust option to select",
                    extra={"session_id": self._session_id},
                )
                return
            if offset:
                self._trust_selected_polls = 0
                if self._trust_arrowed is not None:
                    acted_on, polls = self._trust_arrowed
                    if acted_on == snapshot and polls < _TRUST_ARROW_RETRY_POLLS:
                        self._trust_arrowed = (acted_on, polls + 1)
                        return
                arrow = _ARROW_DOWN if offset > 0 else _ARROW_UP
                await self._runtime.tmux.send_bytes(pane, arrow * abs(offset))
                self._trust_arrowed = (snapshot, 0)
                return
            self._trust_arrowed = None
            self._trust_selected_polls += 1
            if self._trust_selected_polls < _DIALOG_STABLE_TICKS:
                return
            log.info(
                "accepting workspace-trust prompt",
                extra={"session_id": self._session_id},
            )
            await self._runtime.tmux.send_input(pane, "", submit=True)
            return
        self._trust_arrowed = None
        self._trust_selected_polls = 0

        if screen_type in _ESC_DISMISS_LOG:
            # Esc both popups once, after the debounce. For AskUserQuestion the
            # Esc flushes the withheld tool_use so the normalizer can surface it
            # as a card; for the auto-mode modal it takes the modal's cancel
            # path. Neither consents to anything nor is surfaced further.
            sig = screen_type.value
            if self._prev_dialog_sig == sig:
                self._dialog_stable_count += 1
            else:
                self._prev_dialog_sig = sig
                self._dialog_stable_count = 1
            if (
                self._dialog_stable_count >= _DIALOG_STABLE_TICKS
                and self._dismissed_screen is not screen_type
            ):
                log.info(
                    _ESC_DISMISS_LOG[screen_type],
                    extra={"session_id": self._session_id},
                )
                await self._runtime.tmux.send_bytes(pane, b"\x1b")
                self._dismissed_screen = screen_type
            return

        if screen_type is pane_dialog.PaneScreen.PLAN:
            self._departing = None
            await self._poll_plan(session, snapshot)
            return

        if screen_type is not pane_dialog.PaneScreen.APPROVAL:
            # Dialog gone without a Waypoint response (answered or dismissed
            # in the terminal): close its card.
            self._note_departed()
            await self._expire_adoptable()
            await self._plugin.drop_pending_approval(
                self._runtime, self._session_id, _DIALOG_CLOSED_NOTE
            )
            self.forget_dialog()
            self._dismissed_screen = None
            return

        self._departing = None
        dialog = await self._read_approval(pane, snapshot)
        if dialog is None:
            self._prev_dialog_sig = None
            self._dialog_stable_count = 0
            return
        if self._is_stable(dialog.identity):
            await self._surface_approval(dialog)

    def forget_dialog(self) -> None:
        """Treat whatever dialog is on screen as not yet surfaced."""
        self._surfaced = None
        self._prev_dialog_sig = None
        self._dialog_stable_count = 0

    def _note_departed(self) -> None:
        # One poll can catch the TUI mid-redraw, so wait for a second.
        if self._surfaced is not None:
            self._departing = self._surfaced.call
        elif self._departing is not None:
            self._answered.add(self._departing.tool_use_id)
            self._departing = None

    def _pane(self, session: SessionRecord) -> str:
        state = session.transport_state
        return state.get("tmux_pane") or state.get("tmux_session") or self._session_id

    def _is_stable(self, identity: str) -> bool:
        if identity == self._prev_dialog_sig:
            self._dialog_stable_count += 1
        else:
            self._prev_dialog_sig = identity
            self._dialog_stable_count = 1
        return self._dialog_stable_count >= _DIALOG_STABLE_TICKS

    async def _read_approval(
        self, pane: str, snapshot: str
    ) -> pane_dialog.ApprovalDialog | None:
        dialog = pane_dialog.parse_approval(snapshot)
        if dialog is None or dialog.heading is not None:
            return dialog
        try:
            deeper = await self._runtime.tmux.capture_snapshot(pane, _DEEP_CAPTURE_LINE)
        except TmuxError:
            return dialog
        return pane_dialog.parse_approval(deeper) or dialog

    async def _match(self, dialog: pane_dialog.ApprovalDialog) -> PendingCall | None:
        if not dialog.matchable:
            return None
        calls = await asyncio.to_thread(self._calls.candidates, dialog.agent)
        return match_call(dialog, calls, self._answered)

    async def _card_outdated(
        self, dialog: pane_dialog.ApprovalDialog, shown: _Surfaced
    ) -> bool:
        """Whether the open card for the dialog on screen needs raising again."""
        shown.polls += 1
        if shown.call is None:
            # A record written late can still name an unverified card.
            return (
                shown.polls <= _REMATCH_POLLS and await self._match(dialog) is not None
            )
        if shown.polls % _RESOLVED_CHECK_POLLS:
            return False
        # A resolved call's dialog is gone, so this one asks about another.
        return await asyncio.to_thread(self._calls.is_resolved, shown.call)

    async def _replied_dialog_remains(
        self, dialog: pane_dialog.ApprovalDialog, shown: _Surfaced
    ) -> bool:
        """Whether a dialog drawn like the one just replied to needs a card: it
        may not have redrawn yet, belong to the next identical call, or have
        missed the keystroke."""
        if shown.call is not None:
            self._answered.add(shown.call.tool_use_id)
        shown.polls_since_reply += 1
        if shown.polls_since_reply < _REDRAW_POLLS:
            return False
        if await self._match(dialog) is not None:
            return True
        if shown.polls_since_reply < _LOST_REPLY_POLLS:
            return False
        if shown.call is not None:
            self._answered.discard(shown.call.tool_use_id)
        return True

    async def _surface_approval(self, dialog: pane_dialog.ApprovalDialog) -> None:
        pending = self._plugin._pending_approvals.get(self._session_id)
        shown = self._surfaced
        card_open = (
            shown is not None
            and pending is not None
            and pending.approval_id == shown.approval_id
        )
        if shown is not None and shown.identity == dialog.identity:
            if card_open:
                if not await self._card_outdated(dialog, shown):
                    return
            elif not await self._replied_dialog_remains(dialog, shown):
                return

        call = await self._match(dialog)
        signature = f"call:{call.tool_use_id}" if call else f"pane:{dialog.identity}"
        if card_open and pending and shown and pending.signature == signature:
            # The same request drawn differently, as after a resize.
            pending.identity = shown.identity = dialog.identity
            return

        approve_num = dialog.approve_option.number if dialog.approve_option else 1
        decline_opt = dialog.decline_option
        tool_name, tool_input = (
            (call.name, dict(call.input)) if call else _pane_reading(dialog)
        )
        # An unverified card that a late record now names keeps its dialog.
        updated = (
            shown is not None
            and shown.identity == dialog.identity
            and shown.call is None
            and card_open
        )
        approval_id = await self._register_dialog(
            signature,
            dialog.identity,
            call,
            lambda approval_id: PendingTtyApproval(
                approval_id=approval_id,
                tool_name=tool_name,
                approve_number=approve_num,
                decline_number=decline_opt.number if decline_opt else None,
                signature=signature,
                identity=dialog.identity,
                call=call,
            ),
            closing_note=_UPDATED_NOTE if updated else _DIALOG_CLOSED_NOTE,
        )
        if approval_id is None:
            return
        extra: dict[str, Any] = {"subagent": dialog.agent} if dialog.agent else {}
        await self._emit_approval(
            approval_id,
            signature,
            tool_name,
            tool_input,
            call,
            verified=call is not None,
            kind="approval",
            title=f"Approve {tool_name}",
            extra=extra,
        )

    async def _emit_approval(
        self,
        approval_id: str,
        signature: str,
        tool_name: str,
        tool_input: dict[str, Any],
        call: PendingCall | None,
        *,
        verified: bool,
        kind: InteractionKind,
        title: str,
        extra: dict[str, Any],
    ) -> None:
        payload = {"tool_name": tool_name, "tool_input": tool_input}
        text = format_approval_text(payload)
        interaction = InteractionEnvelope(
            kind=kind,
            request_id=approval_id,
            title=title,
            body=tool_input.get("plan") or None if kind == "plan_approval" else text,
            plan_item_id=approval_id if kind == "plan_approval" else None,
            choices=[
                InteractionChoice(label="approve"),
                InteractionChoice(label="decline"),
            ],
        )
        metadata: dict[str, Any] = {
            **payload,
            **extra,
            "approval_id": approval_id,
            "call_verified": verified,
            "dialog_signature": signature,
            "method": "tty_permission",
            "status": SessionStatus.WAITING_INPUT,
            INTERACTION_METADATA_KEY: interaction.to_metadata(),
        }
        if call is not None:
            metadata["tool_use_id"] = call.tool_use_id
        await self._runtime._emit_adapter_event(
            self._session_id,
            EventKind.APPROVAL_REQUEST,
            text,
            metadata,
            SessionStatus.WAITING_INPUT,
        )

    async def still_shows(self, pending: PendingTtyApproval) -> bool:
        """Whether the pane still shows what ``pending``'s ask was raised for, so
        its keys answer that request. Raises ``TmuxError`` when the pane can't be
        read."""
        session = self._runtime.storage.get_session(self._session_id)
        if session is None:
            return False
        pane = self._pane(session)
        snapshot = await self._runtime.tmux.capture_snapshot(pane)
        screen = pane_dialog.classify(snapshot)
        if pending.is_plan:
            plan_dialog = pane_dialog.parse_plan_dialog(snapshot)
            if plan_dialog is None:
                return False
            content = await asyncio.to_thread(self._read_plan, plan_dialog.plan_path)
            option, restore_mode = _plan_approve_option(session, plan_dialog)
            return (
                _plan_signature(plan_dialog.plan_path, content) == pending.signature
                and option is not None
                and option.number == pending.approve_number
                and restore_mode == pending.restore_mode
            )
        if screen is not pane_dialog.PaneScreen.APPROVAL:
            return False
        dialog = await self._read_approval(pane, snapshot)
        if dialog is None or dialog.identity != pending.identity:
            return False
        if pending.call is None:
            return True
        # The same words can come from another call.
        if await asyncio.to_thread(self._calls.is_resolved, pending.call):
            return False
        match = await self._match(dialog)
        return match is not None and match.tool_use_id == pending.call.tool_use_id

    async def _register_dialog(
        self,
        signature: str,
        identity: str,
        call: PendingCall | None,
        make: Callable[[str], PendingTtyApproval],
        *,
        closing_note: str = _DIALOG_CLOSED_NOTE,
    ) -> str | None:
        """Register the dialog on screen as the pending approval.

        Returns the new card's approval id, or None when the dialog adopted a
        card an earlier tailer left open (nothing to emit). A card still open
        for a different request closes first, with ``closing_note``.
        """
        current = self._plugin._pending_approvals.get(self._session_id)
        if current is not None and current.signature != signature:
            await self._plugin.drop_pending_approval(
                self._runtime, self._session_id, closing_note
            )
        adopted = next(
            (approval_id for key, approval_id in self._adoptable if key == signature),
            None,
        )
        self._adoptable = [item for item in self._adoptable if item[1] != adopted]
        await self._expire_adoptable()
        approval_id = adopted or str(uuid.uuid4())
        self._plugin._pending_approvals[self._session_id] = make(approval_id)
        self._surfaced = _Surfaced(identity, signature, approval_id, call)
        if adopted:
            # No event reports the adoption, but the card is answerable again.
            self._runtime.asks.mark_dirty(self._session_id)
        return None if adopted else approval_id

    def _load_adoptable(self) -> None:
        # A new tailer means a new pane or a restarted backend: any in-memory
        # approval is stale, and its card is judged against the pane below.
        self._plugin._pending_approvals.pop(self._session_id, None)
        events = self._runtime.storage.list_approval_events(self._session_id)
        self._adoptable = [
            (
                event.metadata.get("dialog_signature"),
                str(event.metadata.get("approval_id")),
            )
            for event in open_approval_requests(events)
            if event.metadata.get("approval_id")
        ]

    async def _expire_adoptable(self) -> None:
        stale, self._adoptable = self._adoptable, []
        for _, approval_id in stale:
            await self._runtime._record_system_event(
                self._session_id,
                _EXPIRED_NOTE,
                metadata={"method": "approval.invalidated", "approval_id": approval_id},
            )

    async def _close_dialog_state(self) -> None:
        # The tailer stopped on its own (pane gone, session stopped, crash), so
        # no dialog it tracked can still be answered.
        if self._runtime.storage.get_session(self._session_id) is None:
            self._plugin._pending_approvals.pop(self._session_id, None)
            return
        await self._expire_adoptable()
        await self._plugin.drop_pending_approval(
            self._runtime, self._session_id, _EXPIRED_NOTE
        )

    def _read_plan(self, path: str | None) -> str | None:
        """The plan file's text as Claude holds it, or None when it can't be
        read whole."""
        if path is None:
            return None
        tail = self._source.read_tail(path, _PLAN_LIMIT)
        if tail is None or tail.truncated:
            return None
        try:
            text = tail.data.decode()
        except UnicodeDecodeError:
            return None
        return text if text.strip() else None

    async def _poll_plan(self, session: SessionRecord, snapshot: str) -> None:
        """Surface the ExitPlanMode dialog as the same approval card Chat shows.

        The dialog is the plan-mode analogue of a tool-permission prompt. Its
        footer names the plan file, whose text is the plan the card carries,
        matching the Chat card's ``tool_input.plan``; a plan that can't be read
        is left out and the card is unverified. Decline falls through to Esc
        (``decline_number=None``), which keeps plan mode.
        """
        dialog = pane_dialog.parse_plan_dialog(snapshot)
        if dialog is None or dialog.manual_option is None:
            self._prev_dialog_sig = None
            self._dialog_stable_count = 0
            return
        identity = f"plan:{dialog.plan_path}"
        if not self._is_stable(identity):
            return

        content = await asyncio.to_thread(self._read_plan, dialog.plan_path)
        signature = _plan_signature(dialog.plan_path, content)
        pending = self._plugin._pending_approvals.get(self._session_id)
        shown = self._surfaced
        if shown is not None and shown.identity == identity:
            if pending is not None and pending.approval_id == shown.approval_id:
                # A failed read is not a changed plan.
                if signature == shown.signature or content is None:
                    return
            else:
                shown.polls_since_reply += 1
                if shown.polls_since_reply < _REDRAW_POLLS or (
                    signature == shown.signature
                    and shown.polls_since_reply < _LOST_REPLY_POLLS
                ):
                    return

        option, restore_mode = _plan_approve_option(session, dialog)
        assert option is not None
        call = None
        if dialog.plan_path is not None:
            calls = await asyncio.to_thread(self._calls.candidates, None)
            call = match_plan_call(calls, dialog.plan_path, self._answered)
        tool_input: dict[str, Any] = {"plan": content or ""}
        plan_path = call.input.get("planFilePath") if call else dialog.plan_path
        if plan_path:
            tool_input["planFilePath"] = plan_path

        approval_id = await self._register_dialog(
            signature,
            identity,
            call,
            closing_note=(
                _UPDATED_NOTE
                if shown is not None and shown.identity == identity
                else _DIALOG_CLOSED_NOTE
            ),
            make=lambda approval_id: PendingTtyApproval(
                approval_id=approval_id,
                tool_name="ExitPlanMode",
                approve_number=option.number,
                decline_number=None,
                signature=signature,
                call=call,
                is_plan=True,
                restore_mode=restore_mode,
            ),
        )
        if approval_id is None:
            return
        await self._emit_approval(
            approval_id,
            signature,
            "ExitPlanMode",
            tool_input,
            call,
            verified=content is not None,
            kind="plan_approval",
            title="Approve plan",
            extra={},
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
            self._load_adoptable()
            await self._loop()
            if self._stop_requested:
                return
        except Exception:
            log.exception(
                "transcript tailer crashed",
                extra={"session_id": self._session_id},
            )
        # Not on cancel: terminate/restart close the card themselves; after a
        # backend shutdown the next tailer adopts it.
        await self._close_dialog_state()

    async def _loop(self) -> None:
        while True:
            if self._stop_requested:
                await self._drain(force=True)
                return
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
