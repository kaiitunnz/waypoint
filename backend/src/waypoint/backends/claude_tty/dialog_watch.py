"""The dialogs on a Claude TUI pane: raising their asks and checking that a reply
still answers them.

The TUI has no approval channel, so a permission or plan dialog is read off the
pane. The pane only shows it: the call it asks about is looked up in the
transcripts (``pending_calls``) and named when ``call_match`` is certain of it.
Such an ask has ``call_verified`` true and the call's full input; any other
carries the pane's reading.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from waypoint.backends.approvals import open_approval_requests
from waypoint.backends.claude_code.normalize import format_approval_text
from waypoint.backends.claude_tty import pane_dialog
from waypoint.backends.claude_tty.byte_source import TranscriptTreeReader
from waypoint.backends.claude_tty.call_match import (
    calls_shown,
    match_call,
    match_plan_call,
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

# Consecutive identical captures before a dialog is acted on.
_STABLE_TICKS = 2
# Polls without a dialog before the requests seen count as answered: one can
# catch the TUI mid-redraw.
_CLEARED_POLLS = 2
# Polls an unverified card is matched again for a late-written call.
_REMATCH_POLLS = 5
# Polls between checks that a verified card's call is still pending.
_RESOLVED_CHECK_POLLS = 5
# Polls a replied-to dialog is given to redraw.
_REDRAW_POLLS = 2
# Polls after which a replied-to dialog still drawn is raised again regardless.
_LOST_REPLY_POLLS = 5
# Scrollback captured when a tall dialog's heading is above the usual window.
_DEEP_CAPTURE_LINE = -2000
# The most of a plan file read; a longer plan counts as unreadable.
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
STALE_REPLY_NOTE = "Reply not sent: the terminal no longer shows this request"
# The terminal may still show it: what it asks about could not be checked.
UNCONFIRMED_REPLY_NOTE = "Reply not sent: this request couldn't be confirmed"


@dataclass
class PendingTtyApproval:
    """The request a reply to the open card answers."""

    tool_name: str
    approve_number: int
    # None sends Esc.
    decline_number: int | None
    # Which request this is: ``call:<id>``, ``pane:<digest>`` or
    # ``plan:<path>:<digest>``.
    signature: str
    # The dialog on screen: a digest of it as drawn, or ``plan:<path>``.
    identity: str
    # Assigned when the card is registered.
    approval_id: str = ""
    # The call the ask names; None when it is a reading of the pane.
    call: PendingCall | None = None
    # The permission dialog the ask was raised for; None for a plan.
    dialog: pane_dialog.ApprovalDialog | None = None
    # An ExitPlanMode "ready to proceed" dialog.
    is_plan: bool = False
    # The permission mode the plan's approve option lands the pane in.
    restore_mode: str | None = None
    # A reply is being delivered, so the pane is about to move.
    replying: bool = False


@dataclass
class _Surfaced:
    """The dialog the current card was raised for."""

    pending: PendingTtyApproval
    polls: int = 0
    polls_since_reply: int = 0


def pane_target(session: SessionRecord) -> str:
    state = session.transport_state
    return state.get("tmux_pane") or state.get("tmux_session") or session.id


def _plan_signature(path: str | None, content: str | None) -> str:
    text = pane_dialog.digest(content) if content is not None else "unread"
    return f"plan:{path}:{text}"


def _plan_identity(path: str | None) -> str:
    return f"plan:{path}"


def _reply_numbers(dialog: pane_dialog.ApprovalDialog) -> tuple[int, int | None]:
    """The digits approving and declining ``dialog``; without one to decline,
    the reply is Esc."""
    approve = option.number if (option := dialog.approve_option) else 1
    return approve, option.number if (option := dialog.decline_option) else None


def _plan_approve_option(
    session: SessionRecord, dialog: pane_dialog.PlanDialog
) -> tuple[pane_dialog.DialogOption, str] | None:
    """The option approving a plan presses and the mode it lands in, or None for
    a dialog without the manual option.

    Approving restores the pre-plan mode, the way Chat does, as far as the dialog
    can express it: a pre-plan ``auto`` takes the auto option when present.
    Everything else (including ``acceptEdits``/``bypassPermissions``/``dontAsk``,
    which no option maps to, and a launched-in-plan session with no recorded
    prior mode) takes the manual option → ``default``, which never widens
    permissions.
    """
    if (manual := dialog.manual_option) is None:
        return None
    pre_plan = session.transport_state.get("pre_plan_mode")
    if pre_plan == "auto" and dialog.auto_option is not None:
        return dialog.auto_option, "auto"
    return manual, "default"


def _pane_reading(dialog: pane_dialog.ApprovalDialog) -> tuple[str, dict[str, Any]]:
    """Tool name and input as the dialog reads, for an ask that names no call."""
    tool_name = dialog.tool_name or "Unknown"
    target = dialog.target or ""
    match tool_name:
        case "Bash":
            reading: dict[str, Any] = {"command": target}
            if dialog.heading == pane_dialog.UNSANDBOXED_BASH_HEADING:
                reading["dangerouslyDisableSandbox"] = True
            return tool_name, reading
        case "Write" | "Edit" | "MultiEdit":
            return tool_name, {"file_path": target}
        case _:
            return tool_name, {"description": target}


def _text_of(dialog: pane_dialog.ApprovalDialog) -> tuple[str | None, str | None, str]:
    """What a dialog asks, however its lines wrap."""
    lines = [*dialog.described_header, *(dialog.body or [])]
    words = "".join("".join(pane_dialog.unboxed(line).split()) for line in lines)
    return dialog.heading, dialog.agent, words


def _asks_something_else(
    pending: PendingTtyApproval, dialog: pane_dialog.ApprovalDialog
) -> bool:
    """Whether ``dialog`` is not a redraw of what ``pending``'s card asked. One
    that can't be read whole says nothing either way."""
    if not dialog.matchable:
        return False
    if pending.call is not None:
        return not calls_shown(dialog, [pending.call])
    return pending.dialog is not None and _text_of(pending.dialog) != _text_of(dialog)


class DialogWatcher:
    """Tracks the dialog on one session's pane; the plugin holds the pending
    request, which outlives a watcher across a relaunch."""

    def __init__(
        self,
        session_id: str,
        runtime: "SessionRuntime",
        plugin: "ClaudeTtyPlugin",
        source: TranscriptTreeReader,
        calls: CallSource | None = None,
    ) -> None:
        self._session_id = session_id
        self._runtime = runtime
        self._plugin = plugin
        self._source = source
        self._calls = calls or TranscriptCalls(source)
        # What the last capture showed, and how many captures running.
        self._stable_identity: str | None = None
        self._stable_count = 0
        # The snapshot the last trust-prompt arrow burst acted on, and the polls
        # since.
        self._trust_arrowed: tuple[str, int] | None = None
        # Consecutive polls showing the trust option selected.
        self._trust_selected_polls = 0
        # The dialog already raised, held until it leaves the screen so a reply
        # that clears pending before the pane redraws raises no duplicate.
        self._surfaced: _Surfaced | None = None
        # Ids of calls that cannot be asking: replied to, or whose dialog was
        # answered in the terminal. A call stays here once added.
        self._answered: set[str] = set()
        # Dialogs answered while no call was named, by identity, and when. The
        # record of the call may be written late.
        self._unnamed: dict[str, tuple[pane_dialog.ApprovalDialog, float]] = {}
        # Requests raised whose dialogs are not yet known to be answered.
        self._asking: list[PendingTtyApproval] = []
        # Consecutive polls without a dialog.
        self._cleared_polls = 0
        # The popup kind already sent Esc this appearance, so one popup replacing
        # another still gets its own.
        self._dismissed_screen: pane_dialog.PaneScreen | None = None
        # (dialog signature, approval id) of cards an earlier tailer left open:
        # a matching dialog adopts its card; the rest expire on the first decided
        # screen.
        self._adoptable: list[tuple[str | None, str]] = []

    def adopt_open_cards(self) -> None:
        """Take over the approval cards an earlier tailer left open."""
        # A new watcher means a new pane or a restarted backend: any in-memory
        # approval is stale, and its card is judged against the pane.
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

    async def poll(self) -> None:
        """Act on whatever dialog the pane shows.

        The pane is trusted over the stored ``permission_mode``, which holds what
        the last launch passed: the TUI can drift from it (shift+tab, "allow all
        this session"), and a prompt missed that way hangs the session.
        """
        session = self._runtime.storage.get_session(self._session_id)
        if session is None:
            return
        entry = self._plugin._pending_approvals.get(self._session_id)
        if entry is not None and entry.replying:
            return  # our own keystroke is moving the pane

        pane = pane_target(session)
        try:
            snapshot = await self._runtime.tmux.capture_snapshot(pane)
        except TmuxError:
            return
        if self._overtaken(entry):
            return

        screen = pane_dialog.classify(snapshot)
        if screen is not pane_dialog.PaneScreen.TRUST:
            self._trust_arrowed = None
            self._trust_selected_polls = 0
        match screen:
            case pane_dialog.PaneScreen.TRUST:
                await self._accept_trust(pane, snapshot)
            case _ if screen in _ESC_DISMISS_LOG:
                await self._dismiss(pane, screen)
            case pane_dialog.PaneScreen.PLAN:
                self._cleared_polls = 0
                # The plan dialog comes up only once other requests are answered.
                for request in [r for r in self._asking if not r.is_plan]:
                    self.mark_answered(request)
                await self._poll_plan(session, snapshot, entry)
            case pane_dialog.PaneScreen.APPROVAL:
                self._cleared_polls = 0
                await self._poll_approval(pane, snapshot, entry)
            case _:
                await self._on_no_dialog()

    def forget(self) -> None:
        """Treat whatever dialog is on screen as not yet raised."""
        self._surfaced = None
        self._reset_debounce()

    def mark_answered(self, pending: PendingTtyApproval) -> None:
        """Note that ``pending``'s request was answered: its call is not named
        again, even for a dialog that reads the same."""
        self._drop_request(pending)
        if pending.call is not None:
            self._answered.add(pending.call.tool_use_id)
        elif pending.dialog is not None:
            self._unnamed[pending.dialog.identity] = (pending.dialog, time.time())

    def _drop_request(self, pending: PendingTtyApproval) -> None:
        self._asking = [r for r in self._asking if r is not pending]

    async def reply_refusal(self, pending: PendingTtyApproval) -> str | None:
        """Why a reply to ``pending`` must not be sent, as the note for its card;
        None when the pane still shows what the ask was raised for, so its keys
        answer that request. Raises ``TmuxError`` when the pane can't be read.

        The pane is looked at last, after the transcript and plan reads, which
        take long enough for it to move on.
        """
        session = self._runtime.storage.get_session(self._session_id)
        if session is None:
            return UNCONFIRMED_REPLY_NOTE
        pane = pane_target(session)
        if pending.is_plan:
            return await self._plan_refusal(session, pane, pending)
        if pending.call is not None:
            dialog = await self._approval_on_screen(pane)
            if dialog is None or dialog.identity != pending.identity:
                return STALE_REPLY_NOTE
            # The same words can come from another call, with the named one over.
            # A transcript that can't be read matches nothing either.
            match = await self._match(dialog)
            if match is None or match.tool_use_id != pending.call.tool_use_id:
                return UNCONFIRMED_REPLY_NOTE
        dialog = await self._approval_on_screen(pane)
        if (
            dialog is None
            or dialog.identity != pending.identity
            or _reply_numbers(dialog)
            != (pending.approve_number, pending.decline_number)
        ):
            return STALE_REPLY_NOTE
        return None

    async def close(self) -> None:
        """Close the cards of a tailer that stopped on its own (pane gone,
        session stopped, crash): no dialog it tracked can still be answered."""
        if self._runtime.storage.get_session(self._session_id) is None:
            self._plugin._pending_approvals.pop(self._session_id, None)
            return
        await self._expire_adoptable()
        await self._plugin.drop_pending_approval(
            self._runtime, self._session_id, _EXPIRED_NOTE
        )

    # ── screens ──

    async def _accept_trust(self, pane: str, snapshot: str) -> None:
        # A fresh cwd opens at the workspace-trust prompt, which blocks the
        # session until answered. Enter on "No, exit" ends the session, so arrow
        # onto the trust option first. The options wrap, so arrows wait for a
        # redraw (or a dropped-burst timeout) and Enter waits for the selection
        # to hold; a stray Enter after the prompt clears is an empty submit.
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
        if self._trust_selected_polls < _STABLE_TICKS:
            return
        log.info(
            "accepting workspace-trust prompt", extra={"session_id": self._session_id}
        )
        await self._runtime.tmux.send_input(pane, "", submit=True)

    async def _dismiss(self, pane: str, screen: pane_dialog.PaneScreen) -> None:
        # For AskUserQuestion the Esc flushes the withheld tool_use, which the
        # normalizer raises as a card; for the auto-mode modal it takes the
        # cancel path. Neither consents to anything.
        if self._is_stable(screen.value) and self._dismissed_screen is not screen:
            log.info(_ESC_DISMISS_LOG[screen], extra={"session_id": self._session_id})
            await self._runtime.tmux.send_bytes(pane, b"\x1b")
            self._dismissed_screen = screen

    async def _on_no_dialog(self) -> None:
        self._cleared_polls += 1
        if self._cleared_polls == _CLEARED_POLLS:
            for request in list(self._asking):
                self.mark_answered(request)
        await self._expire_adoptable()
        await self._plugin.drop_pending_approval(
            self._runtime, self._session_id, _DIALOG_CLOSED_NOTE
        )
        self.forget()
        self._dismissed_screen = None

    def _overtaken(self, entry: PendingTtyApproval | None) -> bool:
        """Whether a reply or an interrupt has acted on the card since a poll
        that started with ``entry`` as the pending request: what the poll saw is
        then out of date."""
        current = self._plugin._pending_approvals.get(self._session_id)
        return current is not entry or (current is not None and current.replying)

    def _is_stable(self, identity: str) -> bool:
        if identity == self._stable_identity:
            self._stable_count += 1
        else:
            self._stable_identity = identity
            self._stable_count = 1
        return self._stable_count >= _STABLE_TICKS

    def _reset_debounce(self) -> None:
        self._stable_identity = None
        self._stable_count = 0

    # ── tool approvals ──

    async def _poll_approval(
        self, pane: str, snapshot: str, entry: PendingTtyApproval | None
    ) -> None:
        if (dialog := await self._read_approval(pane, snapshot)) is None:
            self._reset_debounce()
        elif self._is_stable(dialog.identity):
            await self._surface_approval(dialog, entry)

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

    async def _approval_on_screen(self, pane: str) -> pane_dialog.ApprovalDialog | None:
        snapshot = await self._runtime.tmux.capture_snapshot(pane)
        return await self._read_approval(pane, snapshot)

    async def _match(self, dialog: pane_dialog.ApprovalDialog) -> PendingCall | None:
        if not dialog.matchable:
            return None
        calls = await asyncio.to_thread(self._calls.candidates, dialog.agent)
        self._claim_late_records(dialog.agent, calls)
        return match_call(dialog, calls, self._answered)

    def _claim_late_records(self, agent: str | None, calls: list[PendingCall]) -> None:
        """Mark as answered the calls that read like a request answered unnamed
        and were made before the answer: any of them may be that request's
        record, written late."""
        for identity, (dialog, answered_at) in list(self._unnamed.items()):
            if dialog.agent != agent:
                continue
            late = [
                call
                for call in calls_shown(dialog, calls, self._answered)
                if call.made_at is None or call.made_at <= answered_at
            ]
            if late:
                self._answered.update(call.tool_use_id for call in late)
                del self._unnamed[identity]

    async def _card_outdated(
        self, dialog: pane_dialog.ApprovalDialog, shown: _Surfaced
    ) -> bool:
        """Whether the open card for the dialog on screen needs raising again."""
        shown.polls += 1
        if (call := shown.pending.call) is None:
            # A record written late can still name an unverified card.
            return (
                shown.polls <= _REMATCH_POLLS and await self._match(dialog) is not None
            )
        if shown.polls % _RESOLVED_CHECK_POLLS:
            return False
        # A resolved call's dialog is gone, so this one asks about another.
        return await asyncio.to_thread(self._calls.is_resolved, call)

    async def _replied_dialog_remains(
        self, dialog: pane_dialog.ApprovalDialog, shown: _Surfaced
    ) -> bool:
        """Whether a dialog drawn like the one just replied to needs a card: it
        may not have redrawn yet, belong to another call that reads the same, or
        have missed the keystroke."""
        shown.polls_since_reply += 1
        if shown.polls_since_reply < _REDRAW_POLLS:
            return False
        if shown.polls_since_reply >= _LOST_REPLY_POLLS:
            return True
        return await self._match(dialog) is not None

    async def _surface_approval(
        self, dialog: pane_dialog.ApprovalDialog, entry: PendingTtyApproval | None
    ) -> None:
        # A request this dialog is no redraw of was answered in the terminal.
        for request in list(self._asking):
            if _asks_something_else(request, dialog):
                self.mark_answered(request)
        shown = self._surfaced
        card_open = shown is not None and self._card_open(shown)
        same_dialog = shown is not None and shown.pending.identity == dialog.identity
        if shown is not None and same_dialog:
            if card_open:
                if not await self._card_outdated(dialog, shown):
                    return
            elif not await self._replied_dialog_remains(dialog, shown):
                return

        call = await self._match(dialog)
        if self._overtaken(entry):
            return
        signature = f"call:{call.tool_use_id}" if call else f"pane:{dialog.identity}"
        approve_number, decline_number = _reply_numbers(dialog)
        if shown is not None and card_open and shown.pending.signature == signature:
            # The same request drawn differently, as after a resize.
            shown.pending.identity = dialog.identity
            shown.pending.dialog = dialog
            shown.pending.approve_number = approve_number
            shown.pending.decline_number = decline_number
            return

        tool_name, tool_input = (
            (call.name, dict(call.input)) if call else _pane_reading(dialog)
        )
        # An unverified card that a late record now names keeps its dialog.
        updated = (
            shown is not None
            and card_open
            and same_dialog
            and shown.pending.call is None
        )
        pending = PendingTtyApproval(
            tool_name=tool_name,
            approve_number=approve_number,
            decline_number=decline_number,
            signature=signature,
            identity=dialog.identity,
            call=call,
            dialog=dialog,
        )
        if shown is not None and updated:
            self._drop_request(shown.pending)  # the same request, now named
        closing_note = _UPDATED_NOTE if updated else _DIALOG_CLOSED_NOTE
        if await self._register(pending, closing_note):
            await self._emit_card(
                pending, tool_input, verified=call is not None, subagent=dialog.agent
            )

    # ── plan approvals ──

    async def _poll_plan(
        self,
        session: SessionRecord,
        snapshot: str,
        entry: PendingTtyApproval | None,
    ) -> None:
        """Raise the ExitPlanMode dialog as the approval card Chat shows.

        The card's plan is the text of the file the dialog's footer names; a plan
        that can't be read is left out and the card is unverified. Decline sends
        Esc, which keeps plan mode.
        """
        dialog = pane_dialog.parse_plan_dialog(snapshot)
        approval = _plan_approve_option(session, dialog) if dialog else None
        if dialog is None or approval is None:
            self._reset_debounce()
            return
        identity = _plan_identity(dialog.plan_path)
        if not self._is_stable(identity):
            return

        content = await asyncio.to_thread(self._read_plan, dialog.plan_path)
        signature = _plan_signature(dialog.plan_path, content)
        shown = self._surfaced
        same_dialog = shown is not None and shown.pending.identity == identity
        if shown is not None and same_dialog:
            unchanged = signature == shown.pending.signature
            if self._card_open(shown):
                # A failed read is not a changed plan.
                if unchanged or content is None:
                    return
            else:
                shown.polls_since_reply += 1
                if shown.polls_since_reply < _REDRAW_POLLS or (
                    unchanged and shown.polls_since_reply < _LOST_REPLY_POLLS
                ):
                    return

        call = None
        if dialog.plan_path is not None:
            calls = await asyncio.to_thread(self._calls.candidates, None)
            call = match_plan_call(calls, dialog.plan_path, self._answered)
        if self._overtaken(entry):
            return
        tool_input: dict[str, Any] = {"plan": content or ""}
        if plan_path := call.input.get("planFilePath") if call else dialog.plan_path:
            tool_input["planFilePath"] = plan_path

        option, restore_mode = approval
        pending = PendingTtyApproval(
            tool_name="ExitPlanMode",
            approve_number=option.number,
            decline_number=None,
            signature=signature,
            identity=identity,
            call=call,
            is_plan=True,
            restore_mode=restore_mode,
        )
        closing_note = _UPDATED_NOTE if same_dialog else _DIALOG_CLOSED_NOTE
        if await self._register(pending, closing_note):
            await self._emit_card(pending, tool_input, verified=content is not None)

    async def _plan_refusal(
        self, session: SessionRecord, pane: str, pending: PendingTtyApproval
    ) -> str | None:
        dialog = await self._plan_on_screen(pane)
        if dialog is None:
            return STALE_REPLY_NOTE
        content = await asyncio.to_thread(self._read_plan, dialog.plan_path)
        if _plan_signature(dialog.plan_path, content) != pending.signature:
            # A plan that can't be read may well be unchanged.
            return UNCONFIRMED_REPLY_NOTE if content is None else STALE_REPLY_NOTE
        dialog = await self._plan_on_screen(pane)
        if dialog is None or _plan_identity(dialog.plan_path) != pending.identity:
            return STALE_REPLY_NOTE
        approval = _plan_approve_option(session, dialog)
        if approval is None or (approval[0].number, approval[1]) != (
            pending.approve_number,
            pending.restore_mode,
        ):
            return STALE_REPLY_NOTE
        return None

    async def _plan_on_screen(self, pane: str) -> pane_dialog.PlanDialog | None:
        snapshot = await self._runtime.tmux.capture_snapshot(pane)
        return pane_dialog.parse_plan_dialog(snapshot)

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

    # ── cards ──

    def _card_open(self, shown: _Surfaced) -> bool:
        return shown.pending is self._plugin._pending_approvals.get(self._session_id)

    async def _register(self, pending: PendingTtyApproval, closing_note: str) -> bool:
        """Make ``pending`` the request a reply answers, and return whether its
        card has to be raised: not when it adopts one left open.

        A card still open for a different request closes with ``closing_note``.
        """
        current = self._plugin._pending_approvals.get(self._session_id)
        if current is not None and current.signature != pending.signature:
            await self._plugin.drop_pending_approval(
                self._runtime, self._session_id, closing_note
            )
        adopted = next(
            (
                approval_id
                for signature, approval_id in self._adoptable
                if signature == pending.signature
            ),
            None,
        )
        self._adoptable = [item for item in self._adoptable if item[1] != adopted]
        await self._expire_adoptable()
        pending.approval_id = adopted or str(uuid.uuid4())
        self._plugin._pending_approvals[self._session_id] = pending
        self._surfaced = _Surfaced(pending)
        self._asking.append(pending)
        if adopted:
            # No event reports the adoption, but the card is answerable again.
            self._runtime.asks.mark_dirty(self._session_id)
        return adopted is None

    async def _expire_adoptable(self) -> None:
        stale, self._adoptable = self._adoptable, []
        for _, approval_id in stale:
            await self._runtime._record_system_event(
                self._session_id,
                _EXPIRED_NOTE,
                metadata={"method": "approval.invalidated", "approval_id": approval_id},
            )

    async def _emit_card(
        self,
        pending: PendingTtyApproval,
        tool_input: dict[str, Any],
        *,
        verified: bool,
        subagent: str | None = None,
    ) -> None:
        payload = {"tool_name": pending.tool_name, "tool_input": tool_input}
        text = format_approval_text(payload)
        kind: InteractionKind = "plan_approval" if pending.is_plan else "approval"
        interaction = InteractionEnvelope(
            kind=kind,
            request_id=pending.approval_id,
            title="Approve plan" if pending.is_plan else f"Approve {pending.tool_name}",
            body=(tool_input.get("plan") or None) if pending.is_plan else text,
            plan_item_id=pending.approval_id if pending.is_plan else None,
            choices=[
                InteractionChoice(label="approve"),
                InteractionChoice(label="decline"),
            ],
        )
        metadata: dict[str, Any] = {
            **payload,
            "approval_id": pending.approval_id,
            "call_verified": verified,
            "dialog_signature": pending.signature,
            "method": "tty_permission",
            "status": SessionStatus.WAITING_INPUT,
            INTERACTION_METADATA_KEY: interaction.to_metadata(),
        }
        if subagent:
            metadata["subagent"] = subagent
        if pending.call is not None:
            metadata["tool_use_id"] = pending.call.tool_use_id
        await self._runtime._emit_adapter_event(
            self._session_id,
            EventKind.APPROVAL_REQUEST,
            text,
            metadata,
            SessionStatus.WAITING_INPUT,
        )
