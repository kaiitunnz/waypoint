"""Cross-session pending asks: every question, tool approval, and plan approval
awaiting the human, across live sessions.

Durable events and the plugin protocols stay the source of truth (see
:mod:`waypoint.pending_questions`, :mod:`waypoint.backends.approvals`, and
``PlanApprovalLifecycle``); this module derives one item list from them, keeps
it current incrementally, and pushes ``pending_asks`` snapshots on the global
sessions stream when it changes.
"""

import asyncio
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from waypoint.backends.approvals import (
    approval_request_id,
    is_approval_resolution,
    open_approval_requests,
)
from waypoint.backends.base import PlanApprovalLifecycle
from waypoint.backends.capabilities import BackendCapabilities
from waypoint.backends.events import INTERACTION_METADATA_KEY, PendingPlanApproval
from waypoint.pending_questions import is_question_lifecycle_event
from waypoint.schemas import (
    EventKind,
    EventRecord,
    PendingAsk,
    PendingAskApproval,
    PendingAskOption,
    PendingAskPlan,
    PendingAskPrompt,
    PendingAskQuestion,
    PendingAsksSnapshot,
    PendingQuestion,
    SessionEnvelope,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.pending_asks")

# Every republish carries every item, so free text in an item is bounded. Plan
# text and question prompts are what the human decides on and stay whole.
TEXT_LIMIT = 2000
_ELLIPSIS = "…"
_RECOMMENDED_SUFFIX = "(recommended)"


def _cut(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + _ELLIPSIS


def _cut_value(value: Any) -> tuple[Any, bool]:
    """A copy of ``value`` with every long string cut, and whether any was."""
    if isinstance(value, str):
        cut = _cut(value)
        return cut, cut != value
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        truncated = False
        for key, item in value.items():
            out[key], was_cut = _cut_value(item)
            truncated = truncated or was_cut
        return out, truncated
    if isinstance(value, list):
        items = [_cut_value(item) for item in value]
        return [item for item, _ in items], any(was_cut for _, was_cut in items)
    return value, False


def _question_prompts(event: EventRecord) -> list[PendingAskPrompt]:
    """The questions an AskUserQuestion call asks, from the canonical
    ``[{question, header?, options, multiSelect?}]`` list every backend stores."""
    payload = event.metadata.get("payload")
    containers = [
        payload.get("input") if isinstance(payload, dict) else None,
        event.metadata.get("tool_input"),
    ]
    raw: list[Any] = []
    for container in containers:
        candidate = container.get("questions") if isinstance(container, dict) else None
        if isinstance(candidate, list) and candidate:
            raw = candidate
            break
    prompts: list[PendingAskPrompt] = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("question"), str):
            continue
        options = []
        for option in entry.get("options") or []:
            if not isinstance(option, dict) or not isinstance(option.get("label"), str):
                continue
            label = option["label"]
            description = option.get("description")
            options.append(
                PendingAskOption(
                    label=label,
                    description=description if isinstance(description, str) else None,
                    recommended=label.strip().lower().endswith(_RECOMMENDED_SUFFIX),
                )
            )
        header = entry.get("header")
        prompts.append(
            PendingAskPrompt(
                question=entry["question"],
                header=header if isinstance(header, str) else None,
                multi_select=entry.get("multiSelect") is True,
                options=options,
            )
        )
    return prompts


def _interaction(event: EventRecord) -> dict[str, Any]:
    raw = event.metadata.get(INTERACTION_METADATA_KEY)
    return raw if isinstance(raw, dict) else {}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass
class _Entry:
    """What the feed knows about one session."""

    asks: list[PendingAsk] = field(default_factory=list)
    # The state the last derivation saw; a change re-derives even an idle entry.
    status: SessionStatus | None = None
    permission_mode: str | None = None
    # The session's own open question ids, kept apart from the question
    # tracker's so reading the feed never makes the tracker treat the session as
    # tracked.
    question_ids: frozenset[str] = frozenset()
    # Open approval events, reused until an ask-relevant event arrives.
    open_approvals: list[EventRecord] | None = None
    plan_plugin: PlanApprovalLifecycle | None = None
    # The plugin's pending plan as of the last derivation (``plan_fresh``), reused
    # until an ask-relevant event arrives or the session's status or mode changes.
    plan: PendingPlanApproval | None = None
    plan_fresh: bool = False
    # Whether the session has items or open question events, so state churn
    # re-derives it.
    watch: bool = False


class PendingAskFeed:
    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        self._entries: dict[str, _Entry] = {}
        # Sessions to re-derive at the next flush; the value says whether an
        # ask-relevant event arrived (so cached open approvals are stale).
        self._dirty: dict[str, bool] = {}
        # ``_active`` once something has asked for the feed (events are then
        # recorded); ``_scanned`` once the first full scan has finished.
        self._active = False
        self._scanned = False
        self._scan_task: asyncio.Task[None] | None = None
        self._stale = False
        self.epoch = secrets.token_hex(4)
        self._revision = 0
        self._published_revision = 0
        self._current: list[PendingAsk] = []

    # ── Reading ─────────────────────────────────────────────────────────

    async def snapshot(self) -> PendingAsksSnapshot:
        await self._ensure_scanned()
        self._refresh(self._take_dirty())
        return self._snapshot()

    def _snapshot(self) -> PendingAsksSnapshot:
        return PendingAsksSnapshot(
            epoch=self.epoch, revision=self._revision, items=list(self._current)
        )

    @staticmethod
    def envelope(snapshot: PendingAsksSnapshot) -> SessionEnvelope:
        return SessionEnvelope(
            type="pending_asks", payload=snapshot.model_dump(mode="json")
        )

    # ── Scan ────────────────────────────────────────────────────────────

    async def _ensure_scanned(self) -> None:
        if not self._scanned:
            # Shielded: a caller cancelled mid-scan (a reconnecting websocket)
            # must not leave a partial feed.
            await asyncio.shield(self._start_scan())

    def _start_scan(self) -> asyncio.Task[None]:
        """The first-scan task, started if none is running. The feed owns it so
        a failed scan is retried by the next read or flush."""
        if self._scan_task is None:
            self._active = True
            self._scan_task = asyncio.create_task(
                self._scan(), name="pending-asks-scan"
            )
            self._scan_task.add_done_callback(self._scan_done)
        return self._scan_task

    def _scan_done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            self._scan_task = None
            log.error("pending-asks scan failed", exc_info=error)

    async def _scan(self) -> None:
        for session in self._runtime.storage.list_live_sessions():
            self._derive_into_entries(session, events_changed=True)
            await asyncio.sleep(0)
        self._scanned = True
        self._refresh(self._take_dirty())
        self._runtime.wake_session_flusher()

    # ── Triggers ────────────────────────────────────────────────────────

    def note_event(self, event: EventRecord) -> None:
        """Flag the session when ``event`` can change its pending asks."""
        entry = self._entries.get(event.session_id)
        if not self._active or entry is None:
            # An unknown session is derived from durable state the first time a
            # flush sees it change.
            return
        try:
            relevant = (
                event.kind is EventKind.APPROVAL_REQUEST
                or is_approval_resolution(event)
                or is_question_lifecycle_event(event, entry.question_ids)
                or (
                    entry.plan_plugin is not None
                    and entry.plan_plugin.affects_plan_approval(event)
                )
            )
        except Exception:
            # Runs on the event-ingest path: re-derive rather than fail the event.
            log.exception(
                "pending-asks event check failed",
                extra={"session_id": event.session_id},
            )
            relevant = True
        if relevant:
            self._mark(event.session_id, events_changed=True)

    def mark_dirty(self, session_id: str) -> None:
        """Re-derive the session at the next flush. For a change in what the
        session can answer that no event reports."""
        self._mark(session_id, events_changed=False)
        self._runtime.wake_session_flusher()

    def mark_watched_dirty(self) -> None:
        """Re-derive every session that has open asks; for session-level
        changes (exit, rename) that reach no event."""
        if not self._active:
            return
        for session_id, entry in self._entries.items():
            if entry.watch:
                self._mark(session_id, events_changed=False)
        self._runtime.wake_session_flusher()

    def forget(self, session_id: str) -> None:
        self._dirty.pop(session_id, None)
        if self._entries.pop(session_id, None) is not None:
            self._stale = True
            self._runtime.wake_session_flusher()

    def _mark(self, session_id: str, *, events_changed: bool) -> None:
        if self._active:
            self._dirty[session_id] = self._dirty.get(session_id, False) or (
                events_changed
            )

    # ── Publication ─────────────────────────────────────────────────────

    async def flush(self, changed_ids: set[str]) -> None:
        """Re-derive what changed and publish the snapshot if it did. Never
        raises: the caller is the session broadcast loop."""
        try:
            if not self._active:
                return
            if not self._scanned:
                # The scan drains these when it finishes.
                self._start_scan()
                for session_id in changed_ids:
                    self._dirty.setdefault(session_id, False)
                return
            targets = self._take_dirty()
            for session_id in changed_ids:
                if session_id not in targets and self._needs_refresh(session_id):
                    targets[session_id] = False
            if targets or self._stale:
                self._refresh(targets)
            if self._revision != self._published_revision:
                self._published_revision = self._revision
                await self._runtime.broadcast.publish(self.envelope(self._snapshot()))
        except Exception:
            log.exception("pending-asks flush failed")

    def _needs_refresh(self, session_id: str) -> bool:
        entry = self._entries.get(session_id)
        if entry is None or entry.watch:
            return True
        session = self._runtime.storage.get_session(session_id)
        if session is None:
            return True
        return (session.status, session.permission_mode) != (
            entry.status,
            entry.permission_mode,
        )

    def _take_dirty(self) -> dict[str, bool]:
        dirty, self._dirty = self._dirty, {}
        return dirty

    # ── Derivation ──────────────────────────────────────────────────────

    def _refresh(self, targets: Mapping[str, bool]) -> None:
        for session_id, events_changed in targets.items():
            session = self._runtime.storage.get_session(session_id)
            if session is None:
                self._entries.pop(session_id, None)
            else:
                self._derive_into_entries(session, events_changed=events_changed)
        self._stale = False
        items = sorted(
            (ask for entry in self._entries.values() for ask in entry.asks),
            key=lambda ask: (ask.created_at, ask.session_id, ask.sequence, ask.id),
        )
        if items != self._current:
            self._current = items
            self._revision += 1

    def _derive_into_entries(
        self, session: SessionRecord, *, events_changed: bool
    ) -> None:
        previous = self._entries.get(session.id)
        try:
            self._entries[session.id] = self._derive(session, previous, events_changed)
        except Exception:
            # One session's failure (an unknown plugin on a legacy row, a
            # plugin bug) must not take the feed down; it just lists nothing.
            log.exception(
                "pending-asks derivation failed", extra={"session_id": session.id}
            )
            self._entries[session.id] = _Entry(
                status=session.status, permission_mode=session.permission_mode
            )

    def _derive(
        self, session: SessionRecord, previous: _Entry | None, events_changed: bool
    ) -> _Entry:
        entry = _Entry(status=session.status, permission_mode=session.permission_mode)
        if (
            session.status is SessionStatus.EXITED
            or session.source is SessionSource.TELEMETRY
        ):
            return entry
        runtime = self._runtime
        plugin = runtime.registry.plugin_for(session)
        if isinstance(plugin, PlanApprovalLifecycle):
            entry.plan_plugin = plugin
        question_ids, questions = runtime.questions.open_questions(session)
        entry.question_ids = frozenset(question_ids)
        asks = [
            ask
            for question in questions
            if (ask := self._question_ask(session, question)) is not None
        ]
        if session.status is not SessionStatus.ERROR:
            transport = runtime.transport_for(session)
            if transport.is_structured:
                asks += self._approval_asks(
                    session, transport, previous, entry, events_changed
                )
                if entry.plan_plugin is not None:
                    plan = self._plan(session, previous, entry, events_changed)
                    if plan is not None:
                        asks.append(self._plan_ask(session, plan))
        entry.asks = asks
        entry.watch = bool(asks) or bool(question_ids)
        return entry

    def _plan(
        self,
        session: SessionRecord,
        previous: _Entry | None,
        entry: _Entry,
        events_changed: bool,
    ) -> PendingPlanApproval | None:
        """The plugin's pending plan. Asking walks the session's events, so the
        last answer stands until an event or a status or mode change."""
        assert entry.plan_plugin is not None
        if (
            previous is not None
            and previous.plan_fresh
            and not events_changed
            and (previous.status, previous.permission_mode)
            == (session.status, session.permission_mode)
        ):
            entry.plan = previous.plan
        else:
            entry.plan = entry.plan_plugin.pending_plan_approval(self._runtime, session)
        entry.plan_fresh = True
        return entry.plan

    def _question_ask(
        self, session: SessionRecord, question: PendingQuestion
    ) -> PendingAsk | None:
        prompts = _question_prompts(question.event)
        if not prompts:
            return None
        interaction = _interaction(question.event)
        title = _text(interaction.get("title")) or prompts[0].question or "Question"
        body = _text(interaction.get("body"))
        return PendingAsk(
            id=f"question:{session.id}:{question.tool_use_id}",
            kind="question",
            **self._common(session, question.event),
            availability=question.availability,
            title=_cut(title),
            body=_cut(body) if body else None,
            question=PendingAskQuestion(
                tool_use_id=question.tool_use_id, questions=prompts
            ),
        )

    def _approval_asks(
        self,
        session: SessionRecord,
        transport: TransportAdapter,
        previous: _Entry | None,
        entry: _Entry,
        events_changed: bool,
    ) -> list[PendingAsk]:
        live = transport.pending_approval_ids(session)
        if (live is None and not transport.has_pending_approval(session)) or (
            live is not None and not live
        ):
            return []
        if (
            previous is not None
            and previous.open_approvals is not None
            and not events_changed
        ):
            entry.open_approvals = previous.open_approvals
        else:
            entry.open_approvals = open_approval_requests(
                self._runtime.storage.list_approval_events(session.id)
            )
        capabilities = self._runtime.registry.capabilities_for(session)
        asks: list[PendingAsk] = []
        for event in entry.open_approvals:
            request_id = approval_request_id(event)
            if request_id is None or (live is not None and request_id not in live):
                continue
            asks.append(self._approval_ask(session, event, request_id, capabilities))
        return asks

    def _approval_ask(
        self,
        session: SessionRecord,
        event: EventRecord,
        request_id: str,
        capabilities: BackendCapabilities,
    ) -> PendingAsk:
        interaction = _interaction(event)
        common = self._common(session, event)
        plan_text = _text(interaction.get("body"))
        if interaction.get("kind") == "plan_approval" and plan_text:
            return PendingAsk(
                id=f"plan_approval:{session.id}:{request_id}",
                kind="plan_approval",
                **common,
                availability="actionable",
                title=_cut(_text(interaction.get("title")) or "Approve plan"),
                plan=PendingAskPlan(
                    plan_id=request_id,
                    text=plan_text,
                    decisions=list(capabilities.approval_decisions),
                    supports_note=capabilities.supports_approval_note,
                    resolve_via="approve",
                ),
            )
        approval = event.metadata.get("approval")
        nested = approval if isinstance(approval, dict) else {}
        tool_name = _text(event.metadata.get("tool_name")) or _text(
            nested.get("tool_name")
        )
        raw_input = event.metadata.get("tool_input") or nested.get("tool_input")
        tool_input, truncated = (
            _cut_value(raw_input) if isinstance(raw_input, dict) else (None, False)
        )
        title = _text(interaction.get("title")) or (
            f"Approve {tool_name}" if tool_name else "Approval requested"
        )
        summary = _cut(event.text)
        return PendingAsk(
            id=f"approval:{session.id}:{request_id}",
            kind="approval",
            **common,
            availability="actionable",
            title=_cut(title),
            body=_cut(_text(interaction.get("body")) or event.text) or None,
            approval=PendingAskApproval(
                approval_id=request_id,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_input_truncated=truncated,
                summary=summary,
                decisions=list(capabilities.approval_decisions),
                supports_note=capabilities.supports_approval_note,
            ),
        )

    def _plan_ask(
        self, session: SessionRecord, plan: PendingPlanApproval
    ) -> PendingAsk:
        return PendingAsk(
            id=f"plan_approval:{session.id}:{plan.plan_id}",
            kind="plan_approval",
            session_id=session.id,
            session_title=session.title,
            session_backend=str(session.backend),
            spawner_session_id=session.spawner_session_id,
            created_at=plan.created_at,
            sequence=plan.sequence,
            availability=plan.availability,
            title="Approve plan",
            plan=PendingAskPlan(
                plan_id=plan.plan_id,
                text=plan.text,
                decisions=plan.decisions,
                supports_note=plan.supports_note,
                resolve_via="approve_plan",
            ),
        )

    @staticmethod
    def _common(session: SessionRecord, event: EventRecord) -> dict[str, Any]:
        return {
            "session_id": session.id,
            "session_title": session.title,
            "session_backend": str(session.backend),
            "spawner_session_id": session.spawner_session_id,
            "created_at": event.ts,
            "sequence": event.sequence,
        }
