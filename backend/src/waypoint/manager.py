"""Waypoint Manager state machine: the durable, DB-backed per-project ticket
scheduler behind ``waypoint manager``.

Two concerns live here:

* The **pure state machine** — the transition table (``_ADJACENCY``), the
  legality/guard checks (:func:`apply_transition`), the server-enforced
  scheduler invariants (:func:`check_invariants`), and the re-anchor policy
  (:func:`compute_next`). These are functions over an in-memory ticket set with
  no I/O, so they are testable without a database.
* :class:`ManagerManager` — CRUD orchestration over storage that wires the pure
  logic to persistence (version-checked writes) and maps every rejection to
  ``HTTPException(409)`` (mirroring ``PresetManager``).

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
    ManagerConfig,
    ManagerInitRequest,
    ManagerNextResponse,
    ManagerRecommendedAction,
    ManagerReconcileReport,
    ManagerStateResponse,
    ManagerTicket,
    ManagerTicketScale,
    ManagerTicketState,
    ManagerTicketTransitions,
    ManagerTreeState,
    ReconcileDeadLead,
    ReconcileIntake,
    ReconcileLatencyTimeout,
    ReconcileStaleGate,
    SessionStatus,
    TicketCreateRequest,
    TicketTransitionRequest,
    TicketUpdateRequest,
)
from waypoint.storage import ManagerTicketConflict, Storage

_S = ManagerTicketState

# A lead session in one of these has settled without living work — reconcile flags
# its ticket as a resume candidate (a missing session counts the same).
_DEAD_SESSION_STATUSES: frozenset[SessionStatus] = frozenset(
    {SessionStatus.EXITED, SessionStatus.ERROR}
)

# Terminal states have no outgoing edges.
_TERMINAL_STATES: frozenset[ManagerTicketState] = frozenset(
    {_S.MERGED, _S.DEFERRED, _S.ABANDONED}
)
# The tech-lead runs in the manager's own working tree (no per-ticket sibling
# worktree), so the tree is a single serial resource: a ticket holds it from the
# moment it is delegated (its branch is checked out) until it reaches a terminal
# state, and no second ticket may occupy it in the meantime. Only the off-tree
# states (intake/triaged/ready and the read-only spec_pending/spec_review, where a
# PRD/RFC writer runs in parallel) leave the tree free.
_TREE_STATES: frozenset[ManagerTicketState] = frozenset(
    {
        _S.DELEGATED,
        _S.BUILDING,
        _S.REVISING,
        _S.BLOCKED,
        _S.REVIEW_REQUESTED,
    }
)
# There is exactly one shared tree, so at most one ticket may occupy it at a time.
# This is intrinsic to the single-tree model, not a tunable — more than one would
# mean two leads editing the same tree.
_TREE_CAPACITY = 1
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
    _S.BLOCKED: frozenset(
        {_S.BUILDING, _S.READY, _S.SPEC_PENDING, _S.ABANDONED, _S.BLOCKED}
    ),
    _S.REVIEW_REQUESTED: frozenset(
        {
            _S.REVISING,
            _S.MERGED,
            _S.DEFERRED,
            _S.ABANDONED,
            _S.BLOCKED,
            _S.REVIEW_REQUESTED,
        }
    ),
    _S.REVISING: frozenset({_S.REVIEW_REQUESTED, _S.BLOCKED, _S.REVISING}),
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


def _holds_tree(ticket: ManagerTicket) -> bool:
    """Whether a ticket occupies the shared working tree.

    A tree-state ticket holds the tree, except a ``blocked`` one with no branch: a
    writer that deems a ``spec_pending`` ticket infeasible escalates it
    ``spec_pending → blocked`` without ever cutting a branch, so that ticket occupies
    no tree and must not block a build from being delegated.
    """
    return ticket.state in _TREE_STATES and not (
        ticket.state == _S.BLOCKED and ticket.branch is None
    )


def tree_state(tickets: Iterable[ManagerTicket]) -> ManagerTreeState:
    held = next((t.id for t in tickets if _holds_tree(t)), None)
    return ManagerTreeState(free=held is None, held_by=held)


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
    scheduler invariants (slot cap, ≤1 spec_pending, unique lead title) are
    enforced separately by :func:`check_invariants` over the whole set.
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
    if request.footprint is not None:
        updates["footprint"] = list(request.footprint)
    if request.deps is not None:
        updates["deps"] = list(request.deps)

    if to in _AWAITING_STATES and not is_self:
        updates["awaiting_since"] = now
    elif to not in _AWAITING_STATES:
        updates["awaiting_since"] = None

    # A non-self transition ends the current gate episode: clear the recorded gate
    # item so the next gate's post records a fresh one and the answer read never
    # resolves this ticket's earlier gate. A self-loop (lead-died resume) keeps it —
    # the gate item is still the live, unanswered one.
    if not is_self:
        updates["inbox_item_id"] = None

    return ticket.model_copy(update=updates)


def check_invariants(tickets: Sequence[ManagerTicket], config: ManagerConfig) -> None:
    """Enforce the server-side scheduler invariants over the whole ticket set."""
    on_tree = sum(1 for t in tickets if _holds_tree(t))
    if on_tree > _TREE_CAPACITY:
        raise ManagerStateError(
            f"working-tree cap exceeded: {on_tree} tickets occupy the shared "
            f"tree (delegated..review_requested) > {_TREE_CAPACITY}"
        )
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
    tree: ManagerTreeState,
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
        if not tree.free:
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
    """Re-anchor: derived tree state, per-ticket legal transitions, and the single
    highest-priority recommended action (priority then FIFO, tree/invariant
    gated), excluding any ticket in this drain's ``tried`` set."""
    tried_set = set(tried)
    tree = tree_state(tickets)
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
        action = _recommended_action(ticket, config, tree, spec_busy)
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
    return ManagerNextResponse(tree=tree, tickets=per_ticket, recommended=recommended)


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
        # init replaces the config wholesale from the manifest, which never
        # carries an owner. Preserve a previously-recorded owner_session_id when
        # this call does not supply one, so re-running init after a manifest edit
        # does not silently drop the session-delete cascade binding.
        config = request.config
        if config.owner_session_id is None:
            existing = self._storage.get_manager_config()
            if existing is not None and existing.owner_session_id is not None:
                config = config.model_copy(
                    update={"owner_session_id": existing.owner_session_id}
                )
        return self._storage.set_manager_config(config)

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
        if request.inbox_item_id is not None:
            updates["inbox_item_id"] = request.inbox_item_id
        if request.is_partial is not None:
            updates["is_partial"] = request.is_partial
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
        """Tear the manager state down: drop every ticket and the persisted config.
        Returns the ticket count removed. Clears state records only — spawned
        sessions, branches, and board channels are reaped separately."""
        removed = self._storage.clear_manager_tickets()
        self._storage.clear_manager_config()
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
        return ManagerStateResponse(
            config=config, tree=tree_state(tickets), tickets=tickets
        )

    def reconcile(self, now: datetime) -> ManagerReconcileReport:
        """Aggregate the drain's server-derivable reconcile signals in one snapshot.

        Read-only: it reports what the manager should adopt (unregistered intake,
        dead leads, latency timeouts, stale gates); the manager still decides and
        acts. External signals (a PR's CI/merge state) stay in the agent's shell.
        """
        config = self.config()
        tickets = self._storage.list_manager_tickets()
        rc = config.render_context
        owner = config.owner_session_id

        intake: list[ReconcileIntake] = []
        if rc is not None and rc.tickets_channel:
            known = {t.id for t in tickets}
            for entry in self._storage.list_board_entries(rc.tickets_channel):
                # Keyless posts are the human's requests; the manager's own registry
                # writes are keyed cells (`ticket:<id>`). A post whose board-entry id
                # is not yet a ticket, by an author other than the manager, is intake.
                if entry.key is not None:
                    continue
                if owner is not None and entry.author_session_id == owner:
                    continue
                if str(entry.id) in known:
                    continue
                intake.append(
                    ReconcileIntake(
                        id=entry.id,
                        author_session_id=entry.author_session_id,
                        text=entry.text,
                    )
                )

        sessions = {s.id: s for s in self._storage.list_sessions()}
        dead_leads: list[ReconcileDeadLead] = []
        for ticket in tickets:
            if ticket.state not in _RESUMABLE_STATES:
                continue
            session = (
                sessions.get(ticket.lead_session_id)
                if ticket.lead_session_id is not None
                else None
            )
            if session is not None and session.status not in _DEAD_SESSION_STATUSES:
                continue
            dead_leads.append(
                ReconcileDeadLead(
                    ticket_id=ticket.id,
                    state=ticket.state,
                    lead_session_id=ticket.lead_session_id,
                    lead_status=session.status.value if session is not None else None,
                )
            )

        latency_timeouts: list[ReconcileLatencyTimeout] = []
        for ticket in tickets:
            if ticket.state not in _AWAITING_STATES or ticket.awaiting_since is None:
                continue
            elapsed = (now - ticket.awaiting_since).total_seconds() / 3600
            if elapsed >= config.human_latency_hours:
                latency_timeouts.append(
                    ReconcileLatencyTimeout(
                        ticket_id=ticket.id,
                        state=ticket.state,
                        awaiting_since=ticket.awaiting_since,
                        hours_elapsed=elapsed,
                    )
                )

        stale_gates: list[ReconcileStaleGate] = []
        for ticket in tickets:
            if ticket.state not in _AWAITING_STATES:
                continue
            if (
                ticket.inbox_item_id is not None
                and self._storage.get_inbox_item(ticket.inbox_item_id) is not None
            ):
                continue
            stale_gates.append(
                ReconcileStaleGate(
                    ticket_id=ticket.id,
                    state=ticket.state,
                    awaiting_since=ticket.awaiting_since,
                )
            )

        return ManagerReconcileReport(
            unregistered_intake=intake,
            dead_leads=dead_leads,
            latency_timeouts=latency_timeouts,
            stale_gates=stale_gates,
        )
