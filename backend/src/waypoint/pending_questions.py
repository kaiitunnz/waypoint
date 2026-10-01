"""Pending AskUserQuestion tracking: snapshots, reconciliation, and the
per-request operation guard.

Durable events are the source of truth (see :mod:`waypoint.questions`). This
module derives the session's open-question snapshot from them, asks the plugin
which questions are live, records a closure for those the provider has ended,
and pushes ``pending_questions`` envelopes when the snapshot changes.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.backends.base import QuestionCancelling
from waypoint.questions import (
    ASK_QUESTION_ANSWER,
    ASK_QUESTION_CANCELLED,
    ASK_QUESTION_CLOSED,
    ASK_USER_QUESTION_TOOL,
    QUESTION_RESOLUTION_NOTE_KINDS,
    QuestionCloseReason,
    QuestionLiveness,
)
from waypoint.schemas import (
    EventKind,
    EventRecord,
    PendingQuestion,
    PendingQuestionAvailability,
    PendingQuestionsSnapshot,
    SessionEnvelope,
    SessionRecord,
    SessionStatus,
)

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.pending_questions")

_Signature = tuple[tuple[str, str], ...]

_AVAILABILITY: dict[QuestionLiveness, PendingQuestionAvailability] = {
    QuestionLiveness.ACTIONABLE: "actionable",
    QuestionLiveness.STARTING: "starting",
    QuestionLiveness.UNAVAILABLE: "unavailable",
}


class PendingQuestionTracker:
    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        # (session_id, tool_use_id) pairs with an answer or cancel in flight.
        self._operations: set[tuple[str, str]] = set()
        self._reconcile_locks: defaultdict[str, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )
        # Sessions whose question state may have changed since the last flush.
        self._dirty: set[str] = set()
        # Last computed open ids, signature, and revision per session.
        self._open_ids: dict[str, frozenset[str]] = {}
        self._signatures: dict[str, tuple[_Signature, int]] = {}
        self._published: dict[str, _Signature] = {}
        self._revision = 0

    # ── Operation guard ──────────────────────────────────────────────────

    @contextmanager
    def operation(self, session_id: str, tool_use_id: str) -> Iterator[None]:
        """Serialize answer/cancel for one request.

        Hold it from provider delivery until the durable answer or cancel event
        is persisted, so a concurrent operation gets a 409 instead of a second
        provider response and reconciliation never closes the request mid-way.
        """
        key = (session_id, tool_use_id)
        if key in self._operations:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="question operation already in progress",
            )
        self._operations.add(key)
        try:
            yield
        finally:
            self._operations.discard(key)
            self.mark_dirty(session_id)

    def require_open(self, session_id: str, tool_use_id: str) -> None:
        if tool_use_id not in self._runtime.storage.open_question_tool_use_ids(
            session_id
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="question is no longer open",
            )

    # ── Durable resolution notes ─────────────────────────────────────────

    async def record_cancelled(
        self,
        session_id: str,
        tool_use_id: str,
        status: SessionStatus | None = None,
    ) -> None:
        await self._runtime._record_system_event(
            session_id,
            "Question cancelled by user",
            status=status,
            metadata={"kind": ASK_QUESTION_CANCELLED, "tool_use_id": tool_use_id},
        )

    async def record_closed(
        self,
        session_id: str,
        tool_use_id: str,
        reason: QuestionCloseReason,
        text: str,
    ) -> None:
        await self._runtime._record_system_event(
            session_id,
            text,
            metadata={
                "kind": ASK_QUESTION_CLOSED,
                "tool_use_id": tool_use_id,
                "reason": reason.value,
            },
        )

    # ── Liveness, snapshot, reconciliation ──────────────────────────────

    def liveness(
        self, session: SessionRecord, tool_use_ids: list[str]
    ) -> dict[str, QuestionLiveness]:
        if not tool_use_ids:
            return {}
        plugin = self._runtime.registry.plugin_for(session)
        if not isinstance(plugin, QuestionCancelling):
            # This driver can never answer (e.g. the session switched from
            # claude_tty to the generic tmux interface), so close the
            # questions instead of stranding them in the dock.
            return dict.fromkeys(tool_use_ids, QuestionLiveness.CLOSED)
        result = plugin.question_liveness(self._runtime, session, tool_use_ids)
        return {
            tid: result.get(tid, QuestionLiveness.UNAVAILABLE) for tid in tool_use_ids
        }

    def require_actionable(self, session: SessionRecord, tool_use_id: str) -> None:
        state = self.liveness(session, [tool_use_id])[tool_use_id]
        if state is QuestionLiveness.ACTIONABLE:
            return
        if state is QuestionLiveness.STARTING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the agent is not ready for a reply to this question yet",
            )
        if state is QuestionLiveness.CLOSED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="question is no longer open",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="session is not running; resume it to act on this question",
        )

    def compute(self, session_id: str) -> PendingQuestionsSnapshot:
        """Read-only snapshot of the session's open questions.

        Questions the provider has ended are omitted; :meth:`reconcile` records
        their closure.
        """
        session = self._runtime.get_session(session_id)
        events = self._runtime.storage.open_question_events(session_id)
        ids = [str(event.metadata["tool_use_id"]) for event in events]
        liveness = self.liveness(session, ids)
        questions = [
            PendingQuestion(
                tool_use_id=tid,
                event=event,
                availability=_AVAILABILITY[liveness[tid]],
            )
            for tid, event in zip(ids, events, strict=True)
            if liveness[tid] is not QuestionLiveness.CLOSED
        ]
        self._open_ids[session_id] = frozenset(ids)
        signature: _Signature = tuple(
            (question.tool_use_id, question.availability) for question in questions
        )
        cached = self._signatures.get(session_id)
        if cached is None or cached[0] != signature:
            self._revision += 1
            cached = (signature, self._revision)
            self._signatures[session_id] = cached
        return PendingQuestionsSnapshot(
            questions=questions,
            as_of_sequence=self._runtime.storage.max_sequence(session_id),
            revision=cached[1],
        )

    async def reconcile(self, session_id: str) -> None:
        """Record a closure for each open question the provider has ended."""
        async with self._reconcile_locks[session_id]:
            session = self._runtime.storage.get_session(session_id)
            if session is None:
                return
            ids = self._runtime.storage.open_question_tool_use_ids(session_id)
            liveness = self.liveness(session, ids)
            for tool_use_id in ids:
                if liveness.get(tool_use_id) is not QuestionLiveness.CLOSED:
                    continue
                # Re-check per id: an answer or cancel may have resolved it, or
                # be resolving it, since the snapshot above.
                if (session_id, tool_use_id) in self._operations:
                    continue
                if tool_use_id not in self._runtime.storage.open_question_tool_use_ids(
                    session_id
                ):
                    continue
                await self.record_closed(
                    session_id,
                    tool_use_id,
                    QuestionCloseReason.PROVIDER_CLOSED,
                    "Question closed: the agent is no longer waiting for an answer",
                )

    # ── Live publication ────────────────────────────────────────────────

    def mark_dirty(self, session_id: str) -> None:
        self._dirty.add(session_id)
        self._runtime._schedule_session_flush(session_id)

    def note_event(self, event: EventRecord) -> None:
        """Flag the session when ``event`` can change its open questions."""
        if is_question_lifecycle_event(event, self._open_ids.get(event.session_id)):
            self._dirty.add(event.session_id)

    async def flush(self, session_id: str) -> None:
        """Reconcile and publish a changed snapshot; called by the debounced
        session-state flusher. Sessions with open questions re-check every
        flush so liveness changes without an event (a permission request
        registering, a pane detaching) still reach clients."""
        if session_id not in self._dirty and not self._open_ids.get(session_id):
            return
        self._dirty.discard(session_id)
        try:
            await self.reconcile(session_id)
            snapshot = self.compute(session_id)
        except HTTPException:
            self.forget(session_id)
            return
        signature = self._signatures[session_id][0]
        if self._published.get(session_id) == signature:
            return
        self._published[session_id] = signature
        await self._runtime.broadcast.publish(
            self.envelope(snapshot), session_id=session_id
        )

    @staticmethod
    def envelope(snapshot: PendingQuestionsSnapshot) -> SessionEnvelope:
        return SessionEnvelope(
            type="pending_questions", payload=snapshot.model_dump(mode="json")
        )

    def forget(self, session_id: str) -> None:
        self._dirty.discard(session_id)
        self._open_ids.pop(session_id, None)
        self._signatures.pop(session_id, None)
        self._published.pop(session_id, None)
        self._reconcile_locks.pop(session_id, None)


def is_question_lifecycle_event(
    event: EventRecord, open_ids: frozenset[str] | None
) -> bool:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    if event.kind is EventKind.TOOL_CALL:
        return metadata.get("tool_name") == ASK_USER_QUESTION_TOOL
    if event.kind is EventKind.USER_INPUT:
        return metadata.get("kind") == ASK_QUESTION_ANSWER
    if event.kind is EventKind.SYSTEM_NOTE:
        return metadata.get("kind") in QUESTION_RESOLUTION_NOTE_KINDS
    if event.kind is EventKind.TOOL_RESULT and open_ids:
        return metadata.get("tool_use_id") in open_ids
    return False
