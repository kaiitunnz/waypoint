"""Tests for the Waypoint Manager state machine: the pure transition table /
invariants / ``next`` policy, plus storage CRUD and ``ManagerManager`` HTTP
mapping, the integration lease, and config init."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from waypoint.manager import (
    _ADJACENCY,
    INTEGRATION_LOCK_NAME,
    ManagerManager,
    ManagerStateError,
    apply_transition,
    check_invariants,
    compute_next,
    legal_targets,
)
from waypoint.schemas import (
    LockRequest,
    ManagerConfig,
    ManagerInitRequest,
    ManagerTicket,
    ManagerTicketScale,
    ManagerTicketState,
    TicketCreateRequest,
    TicketTransitionRequest,
    TicketUpdateRequest,
)
from waypoint.storage import ManagerTicketConflict, Storage

S = ManagerTicketState
Sc = ManagerTicketScale

_TERMINALS = {S.MERGED, S.DEFERRED, S.ABANDONED}
_CONFIG = ManagerConfig(execution_slots=2, max_delegate_attempts=3, max_lead_restarts=3)


def _now() -> datetime:
    return datetime.now(UTC)


def mk(
    state: ManagerTicketState,
    ticket_id: str = "t",
    *,
    priority: str = "p2",
    scale: ManagerTicketScale | None = None,
    attempts: int = 0,
    lead_restarts: int = 0,
    intended_lead_title: str | None = None,
    created_at: datetime | None = None,
) -> ManagerTicket:
    now = created_at or _now()
    return ManagerTicket(
        id=ticket_id,
        title=ticket_id,
        state=state,
        priority=priority,
        scale=scale,
        attempts=attempts,
        lead_restarts=lead_restarts,
        intended_lead_title=intended_lead_title,
        created_at=now,
        updated_at=now,
    )


def _storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "waypoint.db")


def _manager(tmp_path: Path) -> ManagerManager:
    mgr = ManagerManager(_storage(tmp_path))
    mgr.init(ManagerInitRequest(config=_CONFIG))
    return mgr


# ── Transition table: every on-table edge legal, every off-table edge rejected ──


@pytest.mark.parametrize("frm", list(S))
@pytest.mark.parametrize("to", list(S))
def test_transition_legal_iff_on_table(frm: S, to: S) -> None:
    # With a fresh ticket (budgets unspent), a transition succeeds exactly when
    # the target is in the table adjacency; everything else raises. Terminals
    # have empty adjacency, so all their outgoing transitions are rejected.
    ticket = mk(frm, scale=Sc.SUBSTANTIAL)
    request = TicketTransitionRequest(to=to)
    if to in _ADJACENCY[frm]:
        advanced = apply_transition(ticket, request, _CONFIG, _now())
        assert advanced.state == to
    else:
        with pytest.raises(ManagerStateError):
            apply_transition(ticket, request, _CONFIG, _now())


def test_terminal_states_have_no_edges() -> None:
    for terminal in _TERMINALS:
        assert _ADJACENCY[terminal] == frozenset()


def test_illegal_transition_maps_to_409(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    ticket = mgr.create_ticket(TicketCreateRequest(title="x"))
    # intake -> merging is not on the table.
    with pytest.raises(HTTPException) as exc:
        mgr.transition(ticket.id, TicketTransitionRequest(to=S.MERGING))
    assert exc.value.status_code == 409


def test_full_happy_path_walk(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    ticket = mgr.create_ticket(
        TicketCreateRequest(title="ship it", priority="p1", scale=Sc.TRIVIAL)
    )
    walk: list[tuple[ManagerTicketState, dict[str, object]]] = [
        (S.TRIAGED, {"scale": Sc.TRIVIAL}),
        (S.READY, {}),
        (S.DELEGATED, {"intended_lead_title": "subagent:ticket-x:lead"}),
        (S.BUILDING, {"lead_session_id": "sess-1"}),
        (S.REVIEW_REQUESTED, {"pr_url": "http://pr/1"}),
        (S.MERGING, {}),
        (S.MERGED, {}),
    ]
    for to, meta in walk:
        ticket = mgr.transition(ticket.id, TicketTransitionRequest(to=to, **meta))
        assert ticket.state == to
    assert ticket.attempts == 1
    assert ticket.version == len(walk)


def test_legal_targets_matches_adjacency() -> None:
    for state in S:
        assert set(legal_targets(state)) == set(_ADJACENCY[state])


# ── awaiting_since bookkeeping ──────────────────────────────────────────────


def test_awaiting_since_set_on_entry_cleared_on_exit() -> None:
    ticket = mk(S.BUILDING)
    blocked = apply_transition(
        ticket, TicketTransitionRequest(to=S.BLOCKED), _CONFIG, _now()
    )
    assert blocked.awaiting_since is not None
    resumed = apply_transition(
        blocked, TicketTransitionRequest(to=S.BUILDING), _CONFIG, _now()
    )
    assert resumed.awaiting_since is None


def test_awaiting_since_untouched_on_resume_self_loop() -> None:
    stamped = _now() - timedelta(hours=1)
    ticket = mk(S.BLOCKED).model_copy(update={"awaiting_since": stamped})
    resumed = apply_transition(
        ticket, TicketTransitionRequest(to=S.BLOCKED), _CONFIG, _now()
    )
    # The parked lead died; the human is still owed, so the clock keeps running.
    assert resumed.awaiting_since == stamped
    assert resumed.lead_restarts == 1


# ── Budget separation: attempts vs lead_restarts ────────────────────────────


def test_attempts_budget_exhausts_to_blocked_without_touching_restarts() -> None:
    ticket = mk(S.READY)
    now = _now()
    for i in range(_CONFIG.max_delegate_attempts):
        ticket = apply_transition(
            ticket, TicketTransitionRequest(to=S.DELEGATED), _CONFIG, now
        )
        if i < _CONFIG.max_delegate_attempts - 1:
            ticket = apply_transition(
                ticket, TicketTransitionRequest(to=S.READY), _CONFIG, now
            )
    assert ticket.attempts == _CONFIG.max_delegate_attempts
    assert ticket.lead_restarts == 0
    # At the cap the retry edge is refused; only escalation to blocked remains.
    with pytest.raises(ManagerStateError):
        apply_transition(ticket, TicketTransitionRequest(to=S.READY), _CONFIG, now)
    blocked = apply_transition(
        ticket, TicketTransitionRequest(to=S.BLOCKED), _CONFIG, now
    )
    assert blocked.state == S.BLOCKED
    assert blocked.lead_restarts == 0


def test_lead_restart_budget_exhausts_to_blocked_without_touching_attempts() -> None:
    ticket = mk(S.BUILDING)
    now = _now()
    for _ in range(_CONFIG.max_lead_restarts):
        ticket = apply_transition(
            ticket, TicketTransitionRequest(to=S.BUILDING), _CONFIG, now
        )
    assert ticket.lead_restarts == _CONFIG.max_lead_restarts
    assert ticket.attempts == 0
    # A lead death does NOT consume the spawn budget, and at the restart cap the
    # resume self-loop is refused; escalation to blocked remains legal.
    with pytest.raises(ManagerStateError):
        apply_transition(ticket, TicketTransitionRequest(to=S.BUILDING), _CONFIG, now)
    blocked = apply_transition(
        ticket, TicketTransitionRequest(to=S.BLOCKED), _CONFIG, now
    )
    assert blocked.state == S.BLOCKED
    assert blocked.attempts == 0


def test_lead_death_does_not_consume_spawn_budget() -> None:
    # A ticket that dies after working (building self-loop) keeps its attempts.
    ticket = mk(S.BUILDING, attempts=1)
    resumed = apply_transition(
        ticket, TicketTransitionRequest(to=S.BUILDING), _CONFIG, _now()
    )
    assert resumed.attempts == 1
    assert resumed.lead_restarts == 1


# ── Invariants ──────────────────────────────────────────────────────────────


def test_tree_cap_counts_every_on_tree_state() -> None:
    # The shared working tree is held from delegate through terminal, so the
    # awaiting-human states blocked/review_requested occupy it too — not just the
    # active compute states. Two is fine (== cap), a third is a violation.
    check_invariants([mk(S.BUILDING, "a"), mk(S.REVIEW_REQUESTED, "b")], _CONFIG)
    check_invariants([mk(S.BLOCKED, "a"), mk(S.MERGING, "b")], _CONFIG)
    with pytest.raises(ManagerStateError):
        check_invariants(
            [mk(S.BUILDING, "a"), mk(S.REVISING, "b"), mk(S.BLOCKED, "c")], _CONFIG
        )


def test_review_requested_holds_the_tree_serially(tmp_path: Path) -> None:
    # Strict serial on one tree: a ticket parked in review_requested still holds
    # the tree, so a second ticket cannot be delegated until the first terminates.
    mgr = ManagerManager(_storage(tmp_path))
    mgr.init(ManagerInitRequest(config=ManagerConfig(execution_slots=1)))
    a = mgr.create_ticket(TicketCreateRequest(title="a", scale=Sc.TRIVIAL))
    for req in (
        TicketTransitionRequest(to=S.TRIAGED, scale=Sc.TRIVIAL),
        TicketTransitionRequest(to=S.READY),
        TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="a-lead"),
        TicketTransitionRequest(to=S.BUILDING),
        TicketTransitionRequest(to=S.REVIEW_REQUESTED),
    ):
        mgr.transition(a.id, req)
    assert mgr.next().slots.free == 0  # the parked ticket still occupies the tree
    b = mgr.create_ticket(TicketCreateRequest(title="b", scale=Sc.TRIVIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.TRIAGED, scale=Sc.TRIVIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.READY))
    with pytest.raises(HTTPException) as exc:
        mgr.transition(
            b.id, TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="b-lead")
        )
    assert exc.value.status_code == 409


def test_at_most_one_merging() -> None:
    check_invariants([mk(S.MERGING, "a")], _CONFIG)
    with pytest.raises(ManagerStateError):
        check_invariants([mk(S.MERGING, "a"), mk(S.MERGING, "b")], _CONFIG)


def test_at_most_one_spec_pending() -> None:
    check_invariants([mk(S.SPEC_PENDING, "a")], _CONFIG)
    with pytest.raises(ManagerStateError):
        check_invariants([mk(S.SPEC_PENDING, "a"), mk(S.SPEC_PENDING, "b")], _CONFIG)


def test_unique_intended_lead_title_across_live_tickets() -> None:
    a = mk(S.DELEGATED, "a", intended_lead_title="dup")
    b = mk(S.BUILDING, "b", intended_lead_title="dup")
    with pytest.raises(ManagerStateError):
        check_invariants([a, b], _CONFIG)
    # A terminal ticket does not reserve the title.
    terminal = mk(S.MERGED, "a", intended_lead_title="dup")
    check_invariants([terminal, b], _CONFIG)


def test_slot_cap_enforced_through_manager_transition(tmp_path: Path) -> None:
    mgr = ManagerManager(_storage(tmp_path))
    mgr.init(ManagerInitRequest(config=ManagerConfig(execution_slots=1)))
    # Occupy the single slot with ticket A in delegated.
    a = mgr.create_ticket(TicketCreateRequest(title="a", scale=Sc.TRIVIAL))
    mgr.transition(a.id, TicketTransitionRequest(to=S.TRIAGED, scale=Sc.TRIVIAL))
    mgr.transition(a.id, TicketTransitionRequest(to=S.READY))
    mgr.transition(
        a.id, TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="a-lead")
    )
    # B reaches ready; delegating it would make two in compute > 1 → 409.
    b = mgr.create_ticket(TicketCreateRequest(title="b", scale=Sc.TRIVIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.TRIAGED, scale=Sc.TRIVIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.READY))
    with pytest.raises(HTTPException) as exc:
        mgr.transition(
            b.id, TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="b-lead")
        )
    assert exc.value.status_code == 409


def test_duplicate_intended_lead_title_rejected_through_transition(
    tmp_path: Path,
) -> None:
    mgr = ManagerManager(_storage(tmp_path))
    mgr.init(ManagerInitRequest(config=ManagerConfig(execution_slots=5)))
    ids = []
    for name in ("a", "b"):
        t = mgr.create_ticket(TicketCreateRequest(title=name, scale=Sc.TRIVIAL))
        mgr.transition(t.id, TicketTransitionRequest(to=S.TRIAGED, scale=Sc.TRIVIAL))
        mgr.transition(t.id, TicketTransitionRequest(to=S.READY))
        ids.append(t.id)
    mgr.transition(
        ids[0], TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="shared")
    )
    with pytest.raises(HTTPException) as exc:
        mgr.transition(
            ids[1],
            TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="shared"),
        )
    assert exc.value.status_code == 409


# ── next(): enumeration, recommendation, priority/FIFO, gating, tried set ───


def test_next_enumerates_legal_transitions() -> None:
    tickets = [mk(S.BUILDING, "a"), mk(S.MERGED, "done")]
    result = compute_next(tickets, _CONFIG)
    # Terminals are excluded from the live enumeration.
    assert [t.ticket_id for t in result.tickets] == ["a"]
    assert set(result.tickets[0].legal_transitions) == set(_ADJACENCY[S.BUILDING])


def test_next_recommends_highest_priority_then_fifo() -> None:
    older_p1 = mk(
        S.INTAKE, "older", priority="p1", created_at=_now() - timedelta(hours=2)
    )
    newer_p0 = mk(S.INTAKE, "newer", priority="p0", created_at=_now())
    result = compute_next([older_p1, newer_p0], _CONFIG)
    assert result.recommended is not None
    assert result.recommended.ticket_id == "newer"  # p0 beats p1 despite being newer

    a = mk(S.INTAKE, "a", priority="p1", created_at=_now() - timedelta(hours=2))
    b = mk(S.INTAKE, "b", priority="p1", created_at=_now() - timedelta(hours=1))
    result = compute_next([b, a], _CONFIG)
    assert result.recommended is not None
    assert result.recommended.ticket_id == "a"  # FIFO tiebreak on equal priority


def test_next_recommends_delegate_when_slot_free() -> None:
    result = compute_next([mk(S.READY, "r", priority="p0")], _CONFIG)
    assert result.recommended is not None
    assert result.recommended.to_state == S.DELEGATED
    assert result.recommended.event == "delegate"


def test_next_slot_gating_skips_ready_but_still_triages() -> None:
    # Both slots busy: a ready ticket cannot be delegated, but an intake ticket
    # can still be triaged (triage needs no slot).
    tickets = [
        mk(S.BUILDING, "b1"),
        mk(S.BUILDING, "b2"),
        mk(S.READY, "r", priority="p0"),
        mk(S.INTAKE, "i", priority="p3"),
    ]
    result = compute_next(tickets, _CONFIG)
    assert result.slots.free == 0
    assert result.recommended is not None
    assert result.recommended.ticket_id == "i"
    assert result.recommended.to_state == S.TRIAGED


def test_next_no_recommendation_when_only_slot_blocked_ready() -> None:
    tickets = [mk(S.BUILDING, "b1"), mk(S.BUILDING, "b2"), mk(S.READY, "r")]
    result = compute_next(tickets, _CONFIG)
    assert result.recommended is None


def test_next_serial_analysis_gates_second_spec() -> None:
    tickets = [
        mk(S.SPEC_PENDING, "sp"),
        mk(S.TRIAGED, "sub", scale=Sc.SUBSTANTIAL),
    ]
    result = compute_next(tickets, _CONFIG)
    assert result.recommended is None  # a writer is busy; no second spec_pending
    # Without the busy writer, the substantial ticket is recommended for a spec.
    result = compute_next([mk(S.TRIAGED, "sub", scale=Sc.SUBSTANTIAL)], _CONFIG)
    assert result.recommended is not None
    assert result.recommended.to_state == S.SPEC_PENDING


def test_next_triaged_trivial_recommends_ready() -> None:
    result = compute_next([mk(S.TRIAGED, "t", scale=Sc.TRIVIAL)], _CONFIG)
    assert result.recommended is not None
    assert result.recommended.to_state == S.READY


def test_next_excludes_tried_set() -> None:
    a = mk(S.READY, "a", priority="p0", created_at=_now() - timedelta(hours=1))
    b = mk(S.READY, "b", priority="p1", created_at=_now())
    result = compute_next([a, b], _CONFIG, tried=["a"])
    assert result.recommended is not None
    assert result.recommended.ticket_id == "b"


def test_next_derives_slot_state() -> None:
    tickets = [mk(S.DELEGATED, "a"), mk(S.BUILDING, "b"), mk(S.BLOCKED, "c")]
    result = compute_next(tickets, _CONFIG)
    # All three hold the shared tree (delegated..merging spans blocked too).
    assert result.slots.used == 3
    assert result.slots.free == 0


# ── Storage CRUD ────────────────────────────────────────────────────────────


def test_storage_ticket_round_trip(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    ticket = mk(S.DELEGATED, "abc", priority="p0", intended_lead_title="lead")
    storage.create_manager_ticket(ticket)
    fetched = storage.get_manager_ticket("abc")
    assert fetched is not None
    assert fetched.state == S.DELEGATED
    assert fetched.intended_lead_title == "lead"
    assert [t.id for t in storage.list_manager_tickets()] == ["abc"]
    assert [t.id for t in storage.list_manager_tickets(states=[S.BUILDING])] == []


def test_storage_update_bumps_version(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    ticket = mk(S.INTAKE, "abc")
    storage.create_manager_ticket(ticket)
    updated = storage.update_manager_ticket(
        ticket.model_copy(update={"state": S.TRIAGED})
    )
    assert updated.version == 1
    assert updated.state == S.TRIAGED


def test_storage_update_version_conflict(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    ticket = mk(S.INTAKE, "abc")
    storage.create_manager_ticket(ticket)
    storage.update_manager_ticket(ticket)  # bumps to version 1
    # The stale copy (still version 0) now loses the CAS.
    with pytest.raises(ManagerTicketConflict):
        storage.update_manager_ticket(ticket)


def test_get_ticket_404(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    with pytest.raises(HTTPException) as exc:
        mgr.get_ticket("nope")
    assert exc.value.status_code == 404


def test_create_ticket_rejects_unknown_priority(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    with pytest.raises(HTTPException) as exc:
        mgr.create_ticket(TicketCreateRequest(title="x", priority="urgent"))
    assert exc.value.status_code == 400


def test_update_ticket_metadata_only(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    ticket = mgr.create_ticket(TicketCreateRequest(title="x"))
    updated = mgr.update_ticket(
        ticket.id, TicketUpdateRequest(priority="p0", footprint=["src/**"])
    )
    assert updated.priority == "p0"
    assert updated.footprint == ["src/**"]
    assert updated.state == S.INTAKE  # unchanged


# ── Config init ─────────────────────────────────────────────────────────────


def test_config_defaults_when_uninitialized(tmp_path: Path) -> None:
    mgr = ManagerManager(_storage(tmp_path))
    config = mgr.config()
    assert config.execution_slots == 1  # ManagerConfig default (single shared tree)


def test_init_persists_config(tmp_path: Path) -> None:
    mgr = ManagerManager(_storage(tmp_path))
    mgr.init(
        ManagerInitRequest(config=ManagerConfig(execution_slots=4, trunk="develop"))
    )
    config = mgr.config()
    assert config.execution_slots == 4
    assert config.trunk == "develop"


# ── Integration lease: acquire / release / steal-on-expiry ──────────────────


def test_lock_acquire_and_reentrant_refresh(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    lock = mgr.acquire_lock(LockRequest(owner="m1"))
    assert lock.owner == "m1"
    # The same owner may re-acquire (refresh) without contention.
    again = mgr.acquire_lock(LockRequest(owner="m1"))
    assert again.owner == "m1"


def test_lock_acquire_conflict_when_held(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.acquire_lock(LockRequest(owner="m1"))
    with pytest.raises(HTTPException) as exc:
        mgr.acquire_lock(LockRequest(owner="m2"))
    assert exc.value.status_code == 409


def test_lock_release_by_owner(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.acquire_lock(LockRequest(owner="m1"))
    mgr.release_lock(LockRequest(owner="m1"))
    # After release the lock is free again.
    assert mgr.acquire_lock(LockRequest(owner="m2")).owner == "m2"


def test_lock_release_by_non_owner_conflicts(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.acquire_lock(LockRequest(owner="m1"))
    with pytest.raises(HTTPException) as exc:
        mgr.release_lock(LockRequest(owner="m2"))
    assert exc.value.status_code == 409


def test_lock_steal_only_after_ttl_expiry(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    mgr = ManagerManager(storage)
    mgr.init(ManagerInitRequest(config=ManagerConfig(lock_ttl_seconds=900)))
    mgr.acquire_lock(LockRequest(owner="m1"))
    # A live foreign lease cannot be stolen.
    with pytest.raises(HTTPException) as exc:
        mgr.steal_lock(LockRequest(owner="m2"))
    assert exc.value.status_code == 409
    # Force the lease past its TTL, then a steal (after the caller's liveness
    # check) succeeds.
    storage.acquire_integration_lock(
        INTEGRATION_LOCK_NAME, "m1", 900, _now() - timedelta(seconds=1000)
    )
    stolen = mgr.steal_lock(LockRequest(owner="m2"))
    assert stolen.owner == "m2"
