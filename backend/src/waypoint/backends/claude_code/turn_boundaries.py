"""Where a Claude turn ends, and what Waypoint's own interrupt leaves behind.

Shared by the structured adapter, the claude_tty normalizer and history import.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from waypoint.schemas import SessionStatus

# An interrupt writes no terminal assistant record: the native transcript gets a
# ``user`` record whose only block is one of these, after a rejection result when
# a tool call was cut off or declined.
_INTERRUPT_MARKERS = frozenset(
    {"[Request interrupted by user]", "[Request interrupted by user for tool use]"}
)
# ``terminal_reason`` of a stream-json result that an interrupt cut short.
_INTERRUPTED_TERMINAL_REASONS = frozenset({"aborted_streaming", "aborted_tools"})
# Covers a slow remote tail; an interrupt that cancels nothing leaves no trace.
_EXPECTED_INTERRUPT_SECONDS = 30.0

INTERRUPTED_NOTE = "Interrupted"
DECLINED_NOTE = "Turn ended — tool use declined"


def is_interrupt_marker(content: Any) -> bool:
    """A typed prompt is a string, so one quoting a marker does not count."""
    if not isinstance(content, list) or len(content) != 1:
        return False
    block = content[0]
    return (
        isinstance(block, dict)
        and block.get("type") == "text"
        and block.get("text") in _INTERRUPT_MARKERS
    )


def is_user_rejection(record: dict[str, Any]) -> bool:
    """Whether the human declined or cut off this record's tool call, ending the turn.

    A decline with feedback carries an ``Error: …`` result instead, and its turn
    goes on.
    """
    result = record.get("toolUseResult")
    return isinstance(result, str) and result.strip().lower().startswith(
        "user rejected"
    )


def is_interrupted_result(event: dict[str, Any]) -> bool:
    return event.get("terminal_reason") in _INTERRUPTED_TERMINAL_REASONS


def turn_activity_status(event: dict[str, Any]) -> SessionStatus | None:
    """The status a stream-json model or tool message sets. A subagent's keeps the
    session's: a background one goes on after the turn that launched it has ended.
    """
    return None if event.get("parent_tool_use_id") else SessionStatus.RUNNING


def delivery_status(record: dict[str, Any]) -> SessionStatus | None:
    """The status an injected ``user`` record sets: none for one flagged as kept in
    the transcript only, which is never sent to the model.
    """
    return None if record.get("queueTranscriptOnly") else SessionStatus.RUNNING


def turn_end_metadata(stop_reason: str) -> dict[str, Any]:
    return {
        "method": "result",
        "stop_reason": stop_reason,
        "status": SessionStatus.IDLE,
    }


@dataclass
class TurnAbortTracker:
    """Why the interrupt marker about to arrive was written.

    The marker ends the turn; the rejection records before it say how.
    """

    _dismissed_question: bool = False
    _declined: bool = False

    def rejected(self, record: dict[str, Any]) -> None:
        # Absent when the call was already running as the human cut it off.
        if record.get("toolDenialBeforeRun"):
            self._declined = True

    def question_dismissed(self) -> None:
        self._dismissed_question = True

    def resolve(self) -> tuple[str, str] | None:
        """``(note, stop_reason)`` for the marker, or None for a dismissed
        question, which stays answerable. Resets the tracker.
        """
        dismissed, declined = self._dismissed_question, self._declined
        self._dismissed_question = self._declined = False
        if dismissed:
            return None
        if declined:
            return DECLINED_NOTE, "tool_rejected"
        return INTERRUPTED_NOTE, "interrupted"


class InterruptOrigin(Enum):
    """Whose interrupt a marker or aborted result answers."""

    # Waypoint's own: its trace adds no note, as the runtime notes a Stop.
    WAYPOINT = auto()
    # Waypoint's own, with input sent since: it must not move the new turn.
    SUPERSEDED = auto()
    # Anyone else's, such as an Esc in the pane.
    OTHER = auto()


@dataclass
class ExpectedInterrupt:
    """Waypoint's own interrupt, until its marker or aborted result answers it."""

    _deadline: float = 0.0
    # Whether a send has gone through since the interrupt was armed, and the
    # sends under way since then.
    _sent_past: bool = False
    _sending: set[object] = field(default_factory=set)

    @property
    def _superseded(self) -> bool:
        return self._sent_past or bool(self._sending)

    @property
    def pending(self) -> bool:
        return time.monotonic() < self._deadline

    @property
    def unanswered(self) -> bool:
        """Pending, with no input sent since."""
        return self.pending and not self._superseded

    def arm(self) -> None:
        self._deadline = time.monotonic() + _EXPECTED_INTERRUPT_SECONDS
        # A send already under way says nothing about this interrupt.
        self._sent_past = False
        self._sending.clear()

    @contextmanager
    def superseding(self) -> Iterator[None]:
        """Wraps Waypoint sending input past the interrupt. The send supersedes
        it while under way and, unless it raises, from then on.
        """
        send = object()
        self._sending.add(send)
        try:
            yield
            self._sent_past |= send in self._sending
        finally:
            self._sending.discard(send)

    def forget_superseded(self) -> None:
        """A prompt was typed. A superseded interrupt's marker precedes it, so one
        still pending was never written.
        """
        if self._superseded:
            self._deadline = 0.0

    def take(self) -> InterruptOrigin:
        if not self.pending:
            return InterruptOrigin.OTHER
        self._deadline = 0.0
        return (
            InterruptOrigin.SUPERSEDED if self._superseded else InterruptOrigin.WAYPOINT
        )
