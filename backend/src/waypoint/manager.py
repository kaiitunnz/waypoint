"""Waypoint Manager state machine: the durable, DB-backed per-project ticket
scheduler behind ``waypoint manager``.

Two concerns live here:

* The **pure state machine** — the transition table (``_ADJACENCY``), the
  legality/guard checks (:func:`apply_transition`), the server-enforced
  scheduler invariants (:func:`check_invariants`), and the re-anchor policy
  (:func:`compute_next`). These are functions over an in-memory ticket set with
  no I/O, so they are testable without a database.
* :class:`ManagerManager` — CRUD orchestration over storage that wires the pure
  logic to persistence (version-checked writes) and the integration lease, and
  maps every rejection to ``HTTPException(409)`` (mirroring ``PresetManager``).

The transition is keyed by *target state* (matching the RFC CLI
``ticket transition --to <state>``); the table is the ``from -> {to}`` adjacency
and per-edge guards/meta encode the RFC's events. Where the RFC labels two
events with one ``(from, to)`` pair (reject vs latency-timeout into
``abandoned``; done vs partial into ``review_requested``), the distinction rides
in ``reason``/meta, not a separate table key.
"""

import secrets
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from fastapi import HTTPException, status

from waypoint.schemas import (
    IntegrationLock,
    LockRequest,
    ManagerConfig,
    ManagerInitRequest,
    ManagerNextResponse,
    ManagerRecommendedAction,
    ManagerSlotState,
    ManagerStateResponse,
    ManagerTicket,
    ManagerTicketScale,
    ManagerTicketState,
    ManagerTicketTransitions,
    TicketCreateRequest,
    TicketTransitionRequest,
    TicketUpdateRequest,
)
from waypoint.storage import ManagerTicketConflict, Storage

_S = ManagerTicketState

INTEGRATION_LOCK_NAME = "integration"

# Terminal states have no outgoing edges.
_TERMINAL_STATES: frozenset[ManagerTicketState] = frozenset(
    {_S.MERGED, _S.DEFERRED, _S.ABANDONED}
)
# The tech-lead runs in the manager's own working tree (no per-ticket sibling
# worktree), so the tree is a single serial resource: a ticket holds it from the
# moment it is delegated (its branch is checked out) until it reaches a terminal
# state, and no second ticket may occupy it in the meantime. ``execution_slots``
# is the number of such trees — one for the shared-tree model. Only the
# off-tree states (intake/triaged/ready and the read-only spec_pending/
# spec_review, where a PRD/RFC writer runs in parallel) leave the tree free.
_TREE_STATES: frozenset[ManagerTicketState] = frozenset(
    {
        _S.DELEGATED,
        _S.BUILDING,
        _S.REVISING,
        _S.BLOCKED,
        _S.REVIEW_REQUESTED,
        _S.MERGING,
    }
)
# Genuinely awaiting a human decision: ``awaiting_since`` is stamped on entry and
# cleared on exit so a latency timeout only counts real human waits.
_AWAITING_STATES: frozenset[ManagerTicketState] = frozenset(
    {_S.SPEC_REVIEW, _S.BLOCKED, _S.REVIEW_REQUESTED}
)
# States whose self-loop is the lead-died / writer-died resume (a fresh session
# bound to the ticket branch preserved in the working tree), consuming the
# ``lead_restarts`` budget.
_RESUMABLE_STATES: frozenset[ManagerTicketState] = frozenset(
    {
        _S.SPEC_PENDING,
        _S.DELEGATED,
        _S.BUILDING,
        _S.REVISING,
        _S.BLOCKED,
        _S.REVIEW_REQUESTED,
    }
)

# The transition table as data: legal ``to`` states for each ``from`` state.
# Self-loops on the six resumable states are the lead/writer-died resume edge.
_ADJACENCY: dict[ManagerTicketState, frozenset[ManagerTicketState]] = {
    _S.INTAKE: frozenset({_S.TRIAGED}),
    _S.TRIAGED: frozenset({_S.SPEC_PENDING, _S.READY, _S.ABANDONED}),
    _S.SPEC_PENDING: frozenset({_S.SPEC_REVIEW, _S.SPEC_PENDING, _S.BLOCKED}),
    _S.SPEC_REVIEW: frozenset({_S.READY, _S.SPEC_PENDING, _S.ABANDONED}),
    _S.READY: frozenset({_S.DELEGATED}),
    _S.DELEGATED: frozenset({_S.BUILDING, _S.READY, _S.BLOCKED, _S.DELEGATED}),
    _S.BUILDING: frozenset({_S.REVIEW_REQUESTED, _S.BLOCKED, _S.BUILDING}),
    _S.BLOCKED: frozenset({_S.BUILDING, _S.ABANDONED, _S.BLOCKED}),
    _S.REVIEW_REQUESTED: frozenset(
        {_S.REVISING, _S.MERGING, _S.ABANDONED, _S.BLOCKED, _S.REVIEW_REQUESTED}
    ),
    _S.REVISING: frozenset({_S.REVIEW_REQUESTED, _S.BLOCKED, _S.REVISING}),
    _S.MERGING: frozenset({_S.MERGED, _S.DEFERRED, _S.REVISING, _S.BLOCKED}),
    _S.MERGED: frozenset(),
    _S.DEFERRED: frozenset(),
    _S.ABANDONED: frozenset(),
}


class ManagerStateError(Exception):
    """A rejected transition / invariant / policy check (mapped to 409)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def legal_targets(state: ManagerTicketState) -> list[ManagerTicketState]:
    return sorted(_ADJACENCY[state], key=lambda s: s.value)


def is_terminal(state: ManagerTicketState) -> bool:
    return state in _TERMINAL_STATES


def slot_state(
    tickets: Iterable[ManagerTicket], config: ManagerConfig
) -> ManagerSlotState:
    used = sum(1 for t in tickets if t.state in _TREE_STATES)
    total = config.execution_slots
    return ManagerSlotState(total=total, used=used, free=max(0, total - used))


def apply_transition(
    ticket: ManagerTicket,
    request: TicketTransitionRequest,
    config: ManagerConfig,
    now: datetime,
) -> ManagerTicket:
    """Return the ticket advanced by ``request`` (pure; no persistence).

    Validates edge legality and the per-edge budget guards, applies the counter
    increments and provided meta, and maintains ``awaiting_since``. Raises
    :class:`ManagerStateError` for an illegal edge or an exhausted budget. Global
    scheduler invariants (slot cap, ≤1 merging/spec_pending, unique lead title)
    are enforced separately by :func:`check_invariants` over the whole set.
    """
    frm = ticket.state
    to = request.to
    if frm in _TERMINAL_STATES:
        raise ManagerStateError(
            f"ticket {ticket.id} is terminal ({frm.value}); no transitions"
        )
    if to not in _ADJACENCY[frm]:
        raise ManagerStateError(f"illegal transition {frm.value} -> {to.value}")

    updates: dict[str, object] = {"state": to, "updated_at": now}
    is_self = to == frm

    if is_self:
        # Lead/writer-died resume: consume the restart budget, not the spawn one.
        if ticket.lead_restarts >= config.max_lead_restarts:
            raise ManagerStateError(
                f"lead-restart budget exhausted ({ticket.lead_restarts} >= "
                f"{config.max_lead_restarts}); escalate to blocked"
            )
        updates["lead_restarts"] = ticket.lead_restarts + 1
    elif frm == _S.READY and to == _S.DELEGATED:
        # Initial delegate: consume the spawn budget, not the restart one.
        if ticket.attempts >= config.max_delegate_attempts:
            raise ManagerStateError(
                f"delegate budget exhausted ({ticket.attempts} >= "
                f"{config.max_delegate_attempts}); escalate to blocked"
            )
        updates["attempts"] = ticket.attempts + 1
    elif frm == _S.DELEGATED and to == _S.READY:
        # Retry after a spawn failure — only while budget for another delegate
        # remains; otherwise the only legal move out of delegated is -> blocked.
        if ticket.attempts >= config.max_delegate_attempts:
            raise ManagerStateError(
                f"delegate budget exhausted ({ticket.attempts} >= "
                f"{config.max_delegate_attempts}); escalate to blocked"
            )

    if request.scale is not None:
        updates["scale"] = request.scale
    if request.kind is not None:
        updates["kind"] = request.kind
    if request.spec_ref is not None:
        updates["spec_ref"] = request.spec_ref
    if request.intended_lead_title is not None:
        updates["intended_lead_title"] = request.intended_lead_title
    if request.lead_session_id is not None:
        updates["lead_session_id"] = request.lead_session_id
    if request.branch is not None:
        updates["branch"] = request.branch
    if request.pr_url is not None:
        updates["pr_url"] = request.pr_url
    if request.is_partial is not None:
        updates["is_partial"] = request.is_partial
    if request.last_relayed_version is not None:
        updates["last_relayed_version"] = request.last_relayed_version
    if request.footprint is not None:
        updates["footprint"] = list(request.footprint)
    if request.deps is not None:
        updates["deps"] = list(request.deps)

    if to in _AWAITING_STATES and not is_self:
        updates["awaiting_since"] = now
    elif to not in _AWAITING_STATES:
        updates["awaiting_since"] = None

    return ticket.model_copy(update=updates)


def check_invariants(tickets: Sequence[ManagerTicket], config: ManagerConfig) -> None:
    """Enforce the server-side scheduler invariants over the whole ticket set."""
    on_tree = sum(1 for t in tickets if t.state in _TREE_STATES)
    if on_tree > config.execution_slots:
        raise ManagerStateError(
            f"working-tree cap exceeded: {on_tree} tickets occupy the shared "
            f"tree (delegated..merging) > {config.execution_slots}"
        )
    if sum(1 for t in tickets if t.state == _S.MERGING) > 1:
        raise ManagerStateError("at most one ticket may be in 'merging'")
    if sum(1 for t in tickets if t.state == _S.SPEC_PENDING) > 1:
        raise ManagerStateError(
            "at most one ticket may be in 'spec_pending' (serial analysis)"
        )
    titles = [
        t.intended_lead_title
        for t in tickets
        if t.state not in _TERMINAL_STATES and t.intended_lead_title
    ]
    duplicates = sorted({title for title in titles if titles.count(title) > 1})
    if duplicates:
        raise ManagerStateError(
            "intended_lead_title must be unique across live tickets: " f"{duplicates}"
        )
    for ticket in tickets:
        if ticket.attempts > config.max_delegate_attempts:
            raise ManagerStateError(
                f"ticket {ticket.id}: attempts {ticket.attempts} exceeds "
                f"max_delegate_attempts {config.max_delegate_attempts}"
            )
        if ticket.lead_restarts > config.max_lead_restarts:
            raise ManagerStateError(
                f"ticket {ticket.id}: lead_restarts {ticket.lead_restarts} exceeds "
                f"max_lead_restarts {config.max_lead_restarts}"
            )


def _priority_index(priority: str, config: ManagerConfig) -> int:
    # p0..p3 with p0 highest; an unknown priority sorts last.
    try:
        return config.priority_levels.index(priority)
    except ValueError:
        return len(config.priority_levels)


def _recommended_action(
    ticket: ManagerTicket,
    config: ManagerConfig,
    slots: ManagerSlotState,
    spec_busy: bool,
) -> tuple[ManagerTicketState, str, str] | None:
    """The single autonomous forward move the manager should drive for a ticket.

    Only the manager-initiated *pull* transitions are recommended (triage, spec,
    trivial-ready, delegate); the human/lead-driven edges (approve, merge,
    lead-accepted, done, …) are enacted when the manager observes the external
    signal and are surfaced as legal transitions, not recommendations.
    """
    if ticket.state == _S.INTAKE:
        return (_S.TRIAGED, "triage", "new ticket awaiting triage")
    if ticket.state == _S.TRIAGED:
        if ticket.scale == ManagerTicketScale.SUBSTANTIAL:
            if spec_busy:
                return None
            return (_S.SPEC_PENDING, "substantial", "substantial ticket needs a spec")
        if ticket.scale == ManagerTicketScale.TRIVIAL:
            return (_S.READY, "trivial", "trivial ticket is ready to delegate")
        return None
    if ticket.state == _S.READY:
        if slots.free <= 0:
            return None
        if ticket.attempts >= config.max_delegate_attempts:
            return None
        return (_S.DELEGATED, "delegate", "working tree free; delegate to a lead")
    return None


def compute_next(
    tickets: Sequence[ManagerTicket],
    config: ManagerConfig,
    tried: Iterable[str] = (),
) -> ManagerNextResponse:
    """Re-anchor: derived slots, per-ticket legal transitions, and the single
    highest-priority recommended action (priority then FIFO, slot/invariant
    gated), excluding any ticket in this drain's ``tried`` set."""
    tried_set = set(tried)
    slots = slot_state(tickets, config)
    live = [t for t in tickets if t.state not in _TERMINAL_STATES]
    per_ticket = [
        ManagerTicketTransitions(
            ticket_id=t.id,
            priority=t.priority,
            state=t.state,
            legal_transitions=legal_targets(t.state),
        )
        for t in live
    ]
    spec_busy = any(t.state == _S.SPEC_PENDING for t in tickets)
    ordered = sorted(
        live, key=lambda t: (_priority_index(t.priority, config), t.created_at, t.id)
    )
    recommended: ManagerRecommendedAction | None = None
    for ticket in ordered:
        if ticket.id in tried_set:
            continue
        action = _recommended_action(ticket, config, slots, spec_busy)
        if action is not None:
            to_state, event, reason = action
            recommended = ManagerRecommendedAction(
                ticket_id=ticket.id,
                from_state=ticket.state,
                to_state=to_state,
                event=event,
                reason=reason,
            )
            break
    return ManagerNextResponse(slots=slots, tickets=per_ticket, recommended=recommended)


class ManagerManager:
    """CRUD orchestration for the Waypoint Manager state machine."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    @staticmethod
    def _new_id() -> str:
        return f"ticket-{secrets.token_hex(4)}"

    def config(self) -> ManagerConfig:
        return self._storage.get_manager_config() or ManagerConfig()

    def init(self, request: ManagerInitRequest) -> ManagerConfig:
        return self._storage.set_manager_config(request.config)

    def list_tickets(self) -> list[ManagerTicket]:
        return self._storage.list_manager_tickets()

    def get_ticket(self, ticket_id: str) -> ManagerTicket:
        ticket = self._storage.get_manager_ticket(ticket_id)
        if ticket is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown ticket: {ticket_id!r}",
            )
        return ticket

    def create_ticket(self, request: TicketCreateRequest) -> ManagerTicket:
        config = self.config()
        if request.priority not in config.priority_levels:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unknown priority {request.priority!r}; "
                    f"expected one of {config.priority_levels}"
                ),
            )
        now = datetime.now(UTC)
        ticket = ManagerTicket(
            id=request.id or self._new_id(),
            title=request.title,
            priority=request.priority,
            kind=request.kind,
            scale=request.scale,
            state=ManagerTicketState.INTAKE,
            footprint=list(request.footprint),
            deps=list(request.deps),
            created_at=now,
            updated_at=now,
        )
        try:
            return self._storage.create_manager_ticket(ticket)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"a ticket with id {ticket.id!r} already exists",
            ) from exc

    def update_ticket(
        self, ticket_id: str, request: TicketUpdateRequest
    ) -> ManagerTicket:
        ticket = self.get_ticket(ticket_id)
        config = self.config()
        updates: dict[str, object] = {"updated_at": datetime.now(UTC)}
        if request.priority is not None:
            if request.priority not in config.priority_levels:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"unknown priority {request.priority!r}; "
                        f"expected one of {config.priority_levels}"
                    ),
                )
            updates["priority"] = request.priority
        if request.kind is not None:
            updates["kind"] = request.kind
        if request.scale is not None:
            updates["scale"] = request.scale
        if request.footprint is not None:
            updates["footprint"] = list(request.footprint)
        if request.deps is not None:
            updates["deps"] = list(request.deps)
        if request.spec_ref is not None:
            updates["spec_ref"] = request.spec_ref
        if request.intended_lead_title is not None:
            updates["intended_lead_title"] = request.intended_lead_title
        if request.lead_session_id is not None:
            updates["lead_session_id"] = request.lead_session_id
        if request.branch is not None:
            updates["branch"] = request.branch
        if request.pr_url is not None:
            updates["pr_url"] = request.pr_url
        if request.is_partial is not None:
            updates["is_partial"] = request.is_partial
        if request.last_relayed_version is not None:
            updates["last_relayed_version"] = request.last_relayed_version
        updated = ticket.model_copy(update=updates)
        others = [t for t in self._storage.list_manager_tickets() if t.id != ticket_id]
        try:
            check_invariants([*others, updated], config)
        except ManagerStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=exc.detail
            ) from exc
        try:
            return self._storage.update_manager_ticket(updated)
        except ManagerTicketConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"ticket {ticket_id!r} was modified concurrently; retry",
            ) from exc

    def transition(
        self, ticket_id: str, request: TicketTransitionRequest
    ) -> ManagerTicket:
        ticket = self.get_ticket(ticket_id)
        config = self.config()
        now = datetime.now(UTC)
        try:
            advanced = apply_transition(ticket, request, config, now)
            others = [
                t for t in self._storage.list_manager_tickets() if t.id != ticket_id
            ]
            check_invariants([*others, advanced], config)
        except ManagerStateError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=exc.detail
            ) from exc
        try:
            return self._storage.update_manager_ticket(advanced)
        except ManagerTicketConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"ticket {ticket_id!r} was modified concurrently; retry",
            ) from exc

    def delete_ticket(self, ticket_id: str) -> None:
        if not self._storage.delete_manager_ticket(ticket_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"unknown ticket: {ticket_id!r}",
            )

    def deinit(self) -> int:
        """Tear the manager state down: drop every ticket, the persisted config,
        and the integration lease. Returns the ticket count removed. Clears state
        records only — spawned sessions, branches, and board channels are reaped
        separately."""
        removed = self._storage.clear_manager_tickets()
        self._storage.clear_manager_config()
        self._storage.clear_integration_lock(INTEGRATION_LOCK_NAME)
        return removed

    def deinit_if_owner(self, session_id: str) -> bool:
        """Cascade a deinit when the deleted session is the one that ran ``init``
        (so a deleted manager never leaves orphaned backlog state)."""
        config = self._storage.get_manager_config()
        if config is None or config.owner_session_id != session_id:
            return False
        self.deinit()
        return True

    def next(self, tried: Iterable[str] = ()) -> ManagerNextResponse:
        return compute_next(self._storage.list_manager_tickets(), self.config(), tried)

    def state(self) -> ManagerStateResponse:
        config = self._storage.get_manager_config()
        tickets = self._storage.list_manager_tickets()
        slots = slot_state(tickets, config or ManagerConfig())
        lock = self._storage.get_integration_lock(INTEGRATION_LOCK_NAME)
        return ManagerStateResponse(
            config=config, slots=slots, tickets=tickets, lock=lock
        )

    def _lock_ttl(self, request: LockRequest) -> int:
        if request.ttl_seconds is not None:
            return request.ttl_seconds
        return self.config().lock_ttl_seconds

    def acquire_lock(self, request: LockRequest) -> IntegrationLock:
        lock = self._storage.acquire_integration_lock(
            INTEGRATION_LOCK_NAME,
            request.owner,
            self._lock_ttl(request),
            datetime.now(UTC),
        )
        if lock is None:
            held = self._storage.get_integration_lock(INTEGRATION_LOCK_NAME)
            owner = held.owner if held else "another owner"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"integration lock held by {owner!r}",
            )
        return lock

    def steal_lock(self, request: LockRequest) -> IntegrationLock:
        lock = self._storage.steal_integration_lock(
            INTEGRATION_LOCK_NAME,
            request.owner,
            self._lock_ttl(request),
            datetime.now(UTC),
        )
        if lock is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="integration lock is held by a live owner "
                "(steal only after TTL expiry)",
            )
        return lock

    def release_lock(self, request: LockRequest) -> IntegrationLock | None:
        released = self._storage.release_integration_lock(
            INTEGRATION_LOCK_NAME, request.owner, datetime.now(UTC)
        )
        if not released:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="integration lock is held by another live owner",
            )
        return None
