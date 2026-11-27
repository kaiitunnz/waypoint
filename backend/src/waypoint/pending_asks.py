"""Cross-session pending asks: every question, tool approval, and plan approval
awaiting the human, across live sessions."""

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from waypoint.backends.approvals import (
    approval_request_id,
    is_approval_resolution,
    open_approval_requests,
)
from waypoint.backends.base import PendingPlanApproval, PlanApprovalLifecycle
from waypoint.backends.capabilities import BackendCapabilities
from waypoint.backends.events import INTERACTION_METADATA_KEY
from waypoint.pending_questions import is_question_lifecycle_event
from waypoint.schemas import (
    EventKind,
    EventRecord,
    PendingApprovalAsk,
    PendingAsk,
    PendingAskApproval,
    PendingAskOption,
    PendingAskPlan,
    PendingAskPrompt,
    PendingAskQuestion,
    PendingAsksSnapshot,
    PendingPlanAsk,
    PendingQuestion,
    PendingQuestionAsk,
    SessionEnvelope,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.pending_asks")

# Every snapshot carries every item, so free text in an item is cut. Plan text
# and question prompts, which the human decides on, stay whole.
TEXT_LIMIT = 2000
_ELLIPSIS = "…"
_RECOMMENDED_SUFFIX = "(recommended)"


def _cut(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + _ELLIPSIS


def _cut_strings(value: Any) -> Any:
    match value:
        case str():
            return _cut(value)
        case dict():
            return {key: _cut_strings(item) for key, item in value.items()}
        case list():
            return [_cut_strings(item) for item in value]
        case _:
            return value


def _question_prompts(event: EventRecord) -> list[PendingAskPrompt]:
    """The questions of an AskUserQuestion call, from the canonical
    ``payload.input.questions`` list every backend stores."""
    payload = event.metadata.get("payload")
    tool_input = payload.get("input") if isinstance(payload, dict) else None
    raw = tool_input.get("questions") if isinstance(tool_input, dict) else None
    prompts: list[PendingAskPrompt] = []
    for entry in raw if isinstance(raw, list) else []:
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


def _call_identity(event: EventRecord) -> dict[str, Any]:
    """What a request that may not name its call says about it."""
    verified = event.metadata.get("call_verified")
    if not isinstance(verified, bool):
        return {}
    return {
        "call_verified": verified,
        "tool_use_id": _text(event.metadata.get("tool_use_id")),
    }


def _distinct_body(title: str, text: str | None) -> str | None:
    """``text`` cut for the wire, or ``None`` when it is empty or repeats the
    already cut ``title``."""
    body = _cut(text) if text else None
    return body if body != title else None


def _question_ask(
    session: SessionRecord, question: PendingQuestion
) -> PendingQuestionAsk | None:
    prompts = _question_prompts(question.event)
    if not prompts:
        return None
    interaction = _interaction(question.event)
    title = _cut(_text(interaction.get("title")) or prompts[0].question or "Question")
    return PendingQuestionAsk(
        id=f"question:{session.id}:{question.tool_use_id}",
        **_common(session, question.event.ts, question.event.sequence),
        availability=question.availability,
        title=title,
        body=_distinct_body(title, _text(interaction.get("body"))),
        question=PendingAskQuestion(
            tool_use_id=question.tool_use_id, questions=prompts
        ),
    )


def _approval_ask(
    session: SessionRecord,
    event: EventRecord,
    interaction: dict[str, Any],
    request_id: str,
    capabilities: BackendCapabilities,
) -> PendingApprovalAsk:
    tool_name = _text(event.metadata.get("tool_name"))
    raw_input = event.metadata.get("tool_input")
    tool_input = _cut_strings(raw_input) if isinstance(raw_input, dict) else None
    title = _cut(
        _text(interaction.get("title"))
        or (f"Approve {tool_name}" if tool_name else "Approval requested")
    )
    return PendingApprovalAsk(
        id=f"approval:{session.id}:{request_id}",
        **_common(session, event.ts, event.sequence),
        availability="actionable",
        title=title,
        body=_distinct_body(title, _text(interaction.get("body")) or event.text),
        approval=PendingAskApproval(
            approval_id=request_id,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_input_truncated=tool_input is not None and tool_input != raw_input,
            decisions=capabilities.approval_decisions,
            supports_note=capabilities.supports_approval_note,
            subagent=_text(event.metadata.get("subagent")),
            **_call_identity(event),
        ),
    )


def _plan_request_ask(
    session: SessionRecord,
    event: EventRecord,
    interaction: dict[str, Any],
    request_id: str,
    text: str,
    capabilities: BackendCapabilities,
) -> PendingPlanAsk:
    return PendingPlanAsk(
        id=f"plan_approval:{session.id}:{request_id}",
        **_common(session, event.ts, event.sequence),
        availability="actionable",
        title=_cut(_text(interaction.get("title")) or "Approve plan"),
        plan=PendingAskPlan(
            plan_id=request_id,
            text=text,
            decisions=capabilities.approval_decisions,
            supports_note=capabilities.supports_approval_note,
            resolve_via="approve",
            **_call_identity(event),
        ),
    )


def _plan_ask(session: SessionRecord, plan: PendingPlanApproval) -> PendingPlanAsk:
    return PendingPlanAsk(
        id=f"plan_approval:{session.id}:{plan.plan_id}",
        **_common(session, plan.created_at, plan.sequence),
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


def _common(
    session: SessionRecord, created_at: datetime, sequence: int
) -> dict[str, Any]:
    return {
        "session_id": session.id,
        "session_title": session.title,
        "session_backend": session.backend,
        "spawner_session_id": session.spawner_session_id,
        "created_at": created_at,
        "sequence": sequence,
    }


_State = tuple[SessionStatus, str | None]


def _state(session: SessionRecord) -> _State:
    return (session.status, session.permission_mode)


@dataclass
class _Entry:
    # The state the last derivation saw; a change re-derives even an idle entry.
    state: _State
    asks: list[PendingAsk] = field(default_factory=list)
    # Apart from the question tracker's ids so that reading the feed never makes
    # the tracker treat the session as tracked.
    question_ids: frozenset[str] = frozenset()
    plan_plugin: PlanApprovalLifecycle | None = None

    @property
    def watch(self) -> bool:
        return bool(self.asks) or bool(self.question_ids)


@dataclass
class _EventWalks:
    """Answers that cost a walk of the session's events. They stand until an
    ask-relevant event arrives, and the plan also until the session's state
    changes."""

    open_approvals: list[EventRecord] | None = None
    plan: tuple[_State, PendingPlanApproval | None] | None = None


class PendingAskFeed:
    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        self._entries: dict[str, _Entry] = {}
        self._walks: dict[str, _EventWalks] = {}
        self._dirty: set[str] = set()
        # ``_active`` once the feed has been read, before which events are
        # ignored; ``_scanned`` once the first full scan has finished.
        self._active = False
        self._scanned = False
        self._scan_task: asyncio.Task[None] | None = None
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
            epoch=self.epoch, revision=self._revision, items=self._current
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
            self._derive_into_entries(session)
            await asyncio.sleep(0)
        self._scanned = True
        self._refresh(self._take_dirty())
        self._runtime.wake_session_flusher()

    # ── Triggers ────────────────────────────────────────────────────────

    def note_event(self, event: EventRecord) -> None:
        entry = self._entries.get(event.session_id)
        if not self._active or entry is None:
            # An unknown session is derived from durable state the first time a
            # flush sees it change.
            return
        if (
            event.kind is EventKind.APPROVAL_REQUEST
            or is_approval_resolution(event)
            or is_question_lifecycle_event(event, entry.question_ids)
            or self._plan_affected(entry, event)
        ):
            self._walks.pop(event.session_id, None)
            self._dirty.add(event.session_id)

    @staticmethod
    def _plan_affected(entry: _Entry, event: EventRecord) -> bool:
        if entry.plan_plugin is None:
            return False
        try:
            return entry.plan_plugin.affects_plan_approval(event)
        except Exception:
            # Runs on the event-ingest path: re-derive rather than fail the event.
            log.exception(
                "pending-asks plan check failed",
                extra={"session_id": event.session_id},
            )
            return True

    def mark_dirty(self, session_id: str) -> None:
        """Re-derive the session at the next flush. For a change in what the
        session can answer that no event reports, and for a deleted session."""
        if self._active:
            self._dirty.add(session_id)
            self._runtime.wake_session_flusher()

    def mark_watched_dirty(self) -> None:
        """Re-derive every session that has open asks; for session-level
        changes (exit, rename) that reach no event."""
        if not self._active:
            return
        self._dirty.update(
            session_id for session_id, entry in self._entries.items() if entry.watch
        )
        self._runtime.wake_session_flusher()

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
                self._dirty |= changed_ids
                return
            targets = self._take_dirty()
            targets |= {
                session_id
                for session_id in changed_ids - targets
                if self._needs_refresh(session_id)
            }
            if targets:
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
        return _state(session) != entry.state

    def _take_dirty(self) -> set[str]:
        dirty, self._dirty = self._dirty, set()
        return dirty

    # ── Derivation ──────────────────────────────────────────────────────

    def _refresh(self, targets: set[str]) -> None:
        for session_id in targets:
            session = self._runtime.storage.get_session(session_id)
            if session is None:
                self._entries.pop(session_id, None)
                self._walks.pop(session_id, None)
            else:
                self._derive_into_entries(session)
        items = sorted(
            (ask for entry in self._entries.values() for ask in entry.asks),
            key=lambda ask: (ask.created_at, ask.session_id, ask.sequence, ask.id),
        )
        if items != self._current:
            self._current = items
            self._revision += 1

    def _derive_into_entries(self, session: SessionRecord) -> None:
        try:
            self._entries[session.id] = self._derive(session)
        except Exception:
            # One session's failure, such as a plugin bug, must not take the
            # feed down; it just lists nothing.
            log.exception(
                "pending-asks derivation failed", extra={"session_id": session.id}
            )
            self._entries[session.id] = _Entry(state=_state(session))

    def _derive(self, session: SessionRecord) -> _Entry:
        entry = _Entry(state=_state(session))
        if (
            session.status is SessionStatus.EXITED
            or session.source is SessionSource.TELEMETRY
        ):
            self._walks.pop(session.id, None)
            return entry
        runtime = self._runtime
        plugin = runtime.availability.driver(session)
        plan_plugin = plugin if isinstance(plugin, PlanApprovalLifecycle) else None
        entry.plan_plugin = plan_plugin
        question_ids, questions = runtime.questions.open_questions(session)
        entry.question_ids = frozenset(question_ids)
        asks: list[PendingAsk] = [
            ask
            for question in questions
            if (ask := _question_ask(session, question)) is not None
        ]
        if plugin is not None and session.status is not SessionStatus.ERROR:
            transport = runtime.transport_for(session)
            if transport.is_structured:
                asks += self._approval_asks(session, transport)
                if plan_plugin is not None and (
                    plan := self._plan(session, plan_plugin)
                ):
                    asks.append(_plan_ask(session, plan))
        entry.asks = asks
        return entry

    def _plan(
        self, session: SessionRecord, plugin: PlanApprovalLifecycle
    ) -> PendingPlanApproval | None:
        walks = self._walks.setdefault(session.id, _EventWalks())
        state = _state(session)
        if walks.plan is None or walks.plan[0] != state:
            walks.plan = (state, plugin.pending_plan_approval(self._runtime, session))
        return walks.plan[1]

    def _approval_asks(
        self, session: SessionRecord, transport: TransportAdapter
    ) -> list[PendingAsk]:
        live = transport.pending_approval_ids(session)
        if not live:
            return []
        walks = self._walks.setdefault(session.id, _EventWalks())
        if walks.open_approvals is None:
            walks.open_approvals = open_approval_requests(
                self._runtime.storage.list_approval_events(session.id)
            )
        capabilities = self._runtime.registry.capabilities_for(session)
        asks: list[PendingAsk] = []
        for event in walks.open_approvals:
            request_id = approval_request_id(event)
            if request_id is None or request_id not in live:
                continue
            interaction = _interaction(event)
            plan_text = _text(interaction.get("body"))
            if interaction.get("kind") == "plan_approval" and plan_text:
                asks.append(
                    _plan_request_ask(
                        session, event, interaction, request_id, plan_text, capabilities
                    )
                )
            else:
                asks.append(
                    _approval_ask(session, event, interaction, request_id, capabilities)
                )
        return asks
