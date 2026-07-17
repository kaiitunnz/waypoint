"""Tests for the Waypoint Manager state machine: the pure transition table /
invariants / ``next`` policy, plus storage CRUD and ``ManagerManager`` HTTP
mapping, and config init."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from waypoint.manager import (
    _ADJACENCY,
    ManagerManager,
    ManagerRegistry,
    ManagerStateError,
    apply_transition,
    check_invariants,
    compute_next,
    legal_targets,
    tree_state,
)
from waypoint.schemas import (
    InboxMarkdownBlockInput,
    InboxStatus,
    ManagerConfig,
    ManagerInitRequest,
    ManagerRenderContext,
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
_CONFIG = ManagerConfig(max_delegate_attempts=3, max_lead_restarts=3)
# The test manager id; ``mk`` stamps it so storage-seeded tickets are visible to a
# manager scoped to this id.
MID = "mgr-test"


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
    branch: str | None = None,
    created_at: datetime | None = None,
    manager_id: str = MID,
) -> ManagerTicket:
    now = created_at or _now()
    return ManagerTicket(
        id=ticket_id,
        manager_id=manager_id,
        title=ticket_id,
        state=state,
        priority=priority,
        scale=scale,
        attempts=attempts,
        lead_restarts=lead_restarts,
        intended_lead_title=intended_lead_title,
        branch=branch,
        created_at=now,
        updated_at=now,
    )


def _storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "waypoint.db")


def _init(
    storage: Storage, config: ManagerConfig = _CONFIG, mid: str = MID
) -> ManagerManager:
    """Init a manager on ``storage`` under a fixed id and return it scoped."""
    reg = ManagerRegistry(storage)
    reg.init(
        ManagerInitRequest(
            config=config.model_copy(update={"id": mid, "repo_dir": f"/repo/{mid}"})
        )
    )
    return reg.get(mid)


def _manager(tmp_path: Path) -> ManagerManager:
    return _init(_storage(tmp_path))


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
    # intake -> merged is not on the table.
    with pytest.raises(HTTPException) as exc:
        mgr.transition(ticket.id, TicketTransitionRequest(to=S.MERGED))
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
    # One shared tree: at most one ticket occupies it. review_requested and a
    # branch-bearing blocked (a real build that stalled) count too, not just the
    # active compute states.
    check_invariants([mk(S.BUILDING, "a")], _CONFIG)
    check_invariants([mk(S.REVIEW_REQUESTED, "a")], _CONFIG)
    check_invariants([mk(S.BLOCKED, "a", branch="ticket/a")], _CONFIG)
    for pair in (
        (mk(S.BUILDING, "a"), mk(S.REVIEW_REQUESTED, "b")),
        (mk(S.BLOCKED, "a", branch="ticket/a"), mk(S.REVISING, "b")),
        (mk(S.DELEGATED, "a"), mk(S.BUILDING, "b")),
    ):
        with pytest.raises(ManagerStateError):
            check_invariants(list(pair), _CONFIG)


def test_branchless_blocked_does_not_hold_the_tree() -> None:
    # A spec_pending ticket found infeasible escalates to blocked without ever
    # cutting a branch, so it occupies no tree and does not block a build.
    check_invariants([mk(S.BLOCKED, "a"), mk(S.BUILDING, "b")], _CONFIG)
    assert tree_state([mk(S.BLOCKED, "a")]).free is True
    # A concurrent build holds the tree; the branch-less blocked ticket does not.
    tree = tree_state([mk(S.BUILDING, "b"), mk(S.BLOCKED, "a")])
    assert tree.free is False and tree.held_by == "b"
    # A budget-exhausted delegate clears its dropped branch to "", which is also
    # branch-less and holds no tree.
    assert tree_state([mk(S.BLOCKED, "c", branch="")]).free is True


def test_review_requested_holds_the_tree_serially(tmp_path: Path) -> None:
    # Strict serial on one tree: a ticket parked in review_requested still holds
    # the tree, so a second ticket cannot be delegated until the first terminates.
    mgr = _manager(tmp_path)
    a = mgr.create_ticket(TicketCreateRequest(title="a", scale=Sc.TRIVIAL))
    for req in (
        TicketTransitionRequest(to=S.TRIAGED, scale=Sc.TRIVIAL),
        TicketTransitionRequest(to=S.READY),
        TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="a-lead"),
        TicketTransitionRequest(to=S.BUILDING),
        TicketTransitionRequest(to=S.REVIEW_REQUESTED),
    ):
        mgr.transition(a.id, req)
    tree = mgr.next().tree
    assert tree.free is False and tree.held_by == a.id  # parked ticket holds the tree


def test_infeasible_spec_can_block_while_a_build_holds_the_tree(tmp_path: Path) -> None:
    # A substantial ticket is specced in parallel with a build (writers are
    # off-tree); when the writer reports infeasible, spec_pending → blocked must
    # succeed even though another ticket holds the tree — the branch-less blocked
    # ticket occupies no tree.
    mgr = _manager(tmp_path)
    a = mgr.create_ticket(TicketCreateRequest(title="a", scale=Sc.TRIVIAL))
    for req in (
        TicketTransitionRequest(to=S.TRIAGED, scale=Sc.TRIVIAL),
        TicketTransitionRequest(to=S.READY),
        TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="a-lead"),
        TicketTransitionRequest(to=S.BUILDING),
    ):
        mgr.transition(a.id, req)
    b = mgr.create_ticket(TicketCreateRequest(title="b", scale=Sc.SUBSTANTIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.TRIAGED, scale=Sc.SUBSTANTIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.SPEC_PENDING))
    blocked = mgr.transition(
        b.id, TicketTransitionRequest(to=S.BLOCKED, reason="infeasible")
    )
    assert blocked.state == S.BLOCKED
    assert mgr.next().tree.held_by == a.id  # the build still holds the tree, not b
    b = mgr.create_ticket(TicketCreateRequest(title="b", scale=Sc.TRIVIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.TRIAGED, scale=Sc.TRIVIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.READY))
    with pytest.raises(HTTPException) as exc:
        mgr.transition(
            b.id, TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="b-lead")
        )
    assert exc.value.status_code == 409  # a genuine second build is still refused


def test_blocked_revive_edges_spend_no_budget_and_clear_awaiting() -> None:
    # The human can revive a branch-less blocked ticket in place — proceed
    # (`ready`) or re-spec (`spec_pending`) — mirroring the spec gate, with no
    # budget spend, and `awaiting_since` clears on exit.
    blocked = mk(S.BLOCKED).model_copy(update={"awaiting_since": _now()})
    for target in (S.READY, S.SPEC_PENDING):
        revived = apply_transition(
            blocked, TicketTransitionRequest(to=target), _CONFIG, _now()
        )
        assert revived.state == target
        assert revived.awaiting_since is None
        assert revived.attempts == 0 and revived.lead_restarts == 0


def test_blocked_respec_succeeds_free_and_respects_the_spec_pending_cap(
    tmp_path: Path,
) -> None:
    mgr = _manager(tmp_path)
    b = mgr.create_ticket(TicketCreateRequest(title="b", scale=Sc.SUBSTANTIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.TRIAGED, scale=Sc.SUBSTANTIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.SPEC_PENDING))
    mgr.transition(b.id, TicketTransitionRequest(to=S.BLOCKED, reason="infeasible"))
    # With the spec slot free, reviving the blocked ticket to spec_pending succeeds
    # (the new edge; illegal on the pre-change table).
    revived = mgr.transition(b.id, TicketTransitionRequest(to=S.SPEC_PENDING))
    assert revived.state == S.SPEC_PENDING
    # Block it again, occupy the sole spec slot with another ticket, and the re-spec
    # is now rejected by the ≤1-spec_pending cap — not as an illegal edge.
    mgr.transition(b.id, TicketTransitionRequest(to=S.BLOCKED, reason="again"))
    a = mgr.create_ticket(TicketCreateRequest(title="a", scale=Sc.SUBSTANTIAL))
    mgr.transition(a.id, TicketTransitionRequest(to=S.TRIAGED, scale=Sc.SUBSTANTIAL))
    mgr.transition(a.id, TicketTransitionRequest(to=S.SPEC_PENDING))
    with pytest.raises(HTTPException) as exc:
        mgr.transition(b.id, TicketTransitionRequest(to=S.SPEC_PENDING))
    assert exc.value.status_code == 409
    assert "at most one ticket may be in 'spec_pending'" in exc.value.detail


def test_at_most_one_spec_pending() -> None:
    check_invariants([mk(S.SPEC_PENDING, "a")], _CONFIG)
    with pytest.raises(ManagerStateError):
        check_invariants([mk(S.SPEC_PENDING, "a"), mk(S.SPEC_PENDING, "b")], _CONFIG)


def test_unique_intended_lead_title_across_live_tickets() -> None:
    # Isolated from the tree cap: one on-tree ticket plus an off-tree one sharing a
    # title (the tree cap alone would not catch this).
    a = mk(S.DELEGATED, "a", intended_lead_title="dup")
    b = mk(S.READY, "b", intended_lead_title="dup")
    with pytest.raises(ManagerStateError):
        check_invariants([a, b], _CONFIG)
    # A terminal ticket does not reserve the title.
    terminal = mk(S.MERGED, "a", intended_lead_title="dup")
    check_invariants([terminal, b], _CONFIG)


def test_tree_cap_enforced_through_manager_transition(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    # Occupy the tree with ticket A in delegated.
    a = mgr.create_ticket(TicketCreateRequest(title="a", scale=Sc.TRIVIAL))
    mgr.transition(a.id, TicketTransitionRequest(to=S.TRIAGED, scale=Sc.TRIVIAL))
    mgr.transition(a.id, TicketTransitionRequest(to=S.READY))
    mgr.transition(
        a.id, TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="a-lead")
    )
    # B reaches ready; delegating it would put two tickets on the one tree → 409.
    b = mgr.create_ticket(TicketCreateRequest(title="b", scale=Sc.TRIVIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.TRIAGED, scale=Sc.TRIVIAL))
    mgr.transition(b.id, TicketTransitionRequest(to=S.READY))
    with pytest.raises(HTTPException) as exc:
        mgr.transition(
            b.id, TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="b-lead")
        )
    assert exc.value.status_code == 409


# The duplicate-intended-lead-title-through-transition case is unreachable under the
# single-tree cap (a second ticket can never enter delegated while one holds the
# tree); the invariant's logic is covered by the pure
# test_unique_intended_lead_title_across_live_tickets above.


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
    # The tree is busy: a ready ticket cannot be delegated, but an intake ticket
    # can still be triaged (triage needs no tree).
    tickets = [
        mk(S.BUILDING, "b1"),
        mk(S.READY, "r", priority="p0"),
        mk(S.INTAKE, "i", priority="p3"),
    ]
    result = compute_next(tickets, _CONFIG)
    assert result.tree.free is False
    assert result.recommended is not None
    assert result.recommended.ticket_id == "i"
    assert result.recommended.to_state == S.TRIAGED


def test_next_no_recommendation_when_only_tree_blocked_ready() -> None:
    tickets = [mk(S.BUILDING, "b1"), mk(S.READY, "r")]
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


def test_next_derives_tree_state() -> None:
    # A ticket in any on-tree state (delegated..review_requested, a branch-bearing
    # blocked included) holds the single shared tree.
    result = compute_next(
        [mk(S.BLOCKED, "c", branch="ticket/c"), mk(S.INTAKE, "i")], _CONFIG
    )
    assert result.tree.free is False
    assert result.tree.held_by == "c"
    # With nothing on the tree, it is free.
    assert compute_next([mk(S.TRIAGED, "t")], _CONFIG).tree.free is True


def test_reconcile_reports_signals(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    rc = ManagerRenderContext(
        templates_dir="/x", tickets_channel="tickets", ticket_channel_prefix="ticket-"
    )
    mgr = _init(
        storage,
        ManagerConfig(
            owner_session_id="mgr", human_latency_hours=72, render_context=rc
        ),
    )
    # Intake: a keyless human post whose entry id is not a ticket. The manager's own
    # keyed registry cell must be ignored.
    storage.add_board_entry("tickets", "please fix X", author_session_id="human")
    storage.add_board_entry("tickets", "req", key="ticket:zzz", author_session_id="mgr")
    # Drive one ticket onto the tree and into a blocked, awaiting-human state with a
    # ghost lead — it exercises dead_leads and latency at once.
    t = mgr.create_ticket(TicketCreateRequest(title="t", scale=Sc.TRIVIAL))
    mgr.transition(t.id, TicketTransitionRequest(to=S.TRIAGED, scale=Sc.TRIVIAL))
    mgr.transition(t.id, TicketTransitionRequest(to=S.READY))
    mgr.transition(
        t.id,
        TicketTransitionRequest(
            to=S.DELEGATED, intended_lead_title="t-lead", branch="ticket/t"
        ),
    )
    mgr.transition(t.id, TicketTransitionRequest(to=S.BUILDING))
    mgr.transition(t.id, TicketTransitionRequest(to=S.BLOCKED, reason="stuck"))
    mgr.update_ticket(t.id, TicketUpdateRequest(lead_session_id="ghost"))

    report = mgr.reconcile(datetime.now(UTC) + timedelta(hours=100))

    assert [i.text for i in report.unregistered_intake] == ["please fix X"]
    dead = [d for d in report.dead_leads if d.ticket_id == t.id]
    assert dead and dead[0].lead_session_id == "ghost" and dead[0].lead_status is None
    latency = [x for x in report.latency_timeouts if x.ticket_id == t.id]
    assert latency and latency[0].hours_elapsed >= 72


def test_reconcile_surfaces_intake_priority(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    rc = ManagerRenderContext(
        templates_dir="/x", tickets_channel="tickets", ticket_channel_prefix="ticket-"
    )
    mgr = _init(storage, ManagerConfig(owner_session_id="mgr", render_context=rc))
    storage.add_board_entry(
        "tickets", "high one", author_session_id="human", metadata={"priority": "p1"}
    )
    storage.add_board_entry("tickets", "plain one", author_session_id="human")
    storage.add_board_entry(
        "tickets",
        "bogus one",
        author_session_id="human",
        metadata={"priority": "urgent"},
    )

    intake = {i.text: i.priority for i in mgr.reconcile(_now()).unregistered_intake}

    assert intake == {"high one": "p1", "plain one": None, "bogus one": None}


def test_reconcile_reports_stale_gates(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    mgr = _init(storage)
    # An awaiting ticket with no recorded gate item — a crash between the awaiting
    # transition and the inbox post.
    storage.create_manager_ticket(mk(S.SPEC_REVIEW, "g1"))
    # An awaiting ticket whose recorded item exists — a live gate, not stale.
    item = storage.create_inbox_item(
        "mgr", None, "ticket-g2: t — spec review", [InboxMarkdownBlockInput(text="x")]
    )
    storage.create_manager_ticket(
        mk(S.SPEC_REVIEW, "g2").model_copy(update={"inbox_item_id": item.id})
    )
    # An awaiting ticket whose recorded item is gone.
    storage.create_manager_ticket(
        mk(S.BLOCKED, "g3").model_copy(update={"inbox_item_id": "missing"})
    )
    # A non-awaiting ticket with no item — never a gate.
    storage.create_manager_ticket(mk(S.BUILDING, "g4"))

    report = mgr.reconcile(_now())

    assert {g.ticket_id for g in report.stale_gates} == {"g1", "g3"}


def test_reconcile_reports_finalize_pending(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    mgr = _init(storage)
    # On-tree terminals still holding a branch — a crash between the terminal record
    # and the reap. `abandoned` covers the restart-exhaustion path that keeps its branch.
    storage.create_manager_ticket(mk(S.MERGED, "m", branch="ticket/m"))
    storage.create_manager_ticket(mk(S.ABANDONED, "a", branch="ticket/a"))
    # A finalized terminal (branch cleared) and an off-tree terminal (never delegated):
    # nothing to reap.
    storage.create_manager_ticket(mk(S.MERGED, "done", branch=""))
    storage.create_manager_ticket(mk(S.ABANDONED, "reject"))

    report = mgr.reconcile(_now())

    assert {f.ticket_id for f in report.finalize_pending} == {"m", "a"}
    # Terminals never surface in the resume or awaiting signals.
    assert not report.dead_leads
    assert not report.latency_timeouts
    assert not report.stale_gates


def test_reconcile_skips_branchless_blocked_dead_lead(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    mgr = _init(storage)
    # An infeasible spec parked in blocked: no branch, and its reaped writer's id
    # lingers. It awaits a human decision, so it is not a lead to resume.
    storage.create_manager_ticket(
        mk(S.BLOCKED, "inf").model_copy(update={"lead_session_id": "dead-writer"})
    )
    # A budget-exhausted delegate parked in blocked with its dropped branch cleared
    # to "" — also branch-less, also not a resume candidate.
    storage.create_manager_ticket(
        mk(S.BLOCKED, "exh", branch="").model_copy(
            update={"lead_session_id": "dead-lead"}
        )
    )
    # A mid-build blocked ticket does hold a branch — a genuine dead-lead resume.
    storage.create_manager_ticket(
        mk(S.BLOCKED, "mid", branch="ticket/mid").model_copy(
            update={"lead_session_id": "dead-lead"}
        )
    )

    dead = {d.ticket_id for d in mgr.reconcile(_now()).dead_leads}

    assert dead == {"mid"}


def test_reset_attempts_zeroes_the_delegate_budget(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    mgr = _init(storage)
    # A ticket blocked after exhausting the delegate budget; a human fixes the
    # launch config and retries.
    storage.create_manager_ticket(mk(S.BLOCKED, "b", attempts=3, lead_restarts=2))

    mgr.update_ticket("b", TicketUpdateRequest(reset_attempts=True))

    t = storage.get_manager_ticket("b")
    assert t is not None
    assert t.attempts == 0
    assert t.lead_restarts == 2  # the in-build resume budget is untouched


def test_reconcile_skips_restart_exhausted_blocked_dead_lead(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    mgr = _init(storage)
    # A blocked ticket whose lead kept dying until the restart budget was spent keeps its
    # branch (committed work) and waits on the human's retry/abandon gate.
    storage.create_manager_ticket(
        mk(
            S.BLOCKED,
            "exh",
            branch="ticket/exh",
            lead_restarts=_CONFIG.max_lead_restarts,
        ).model_copy(update={"lead_session_id": "dead-lead"})
    )
    # A blocked ticket with budget remaining is a genuine dead-lead resume.
    storage.create_manager_ticket(
        mk(
            S.BLOCKED,
            "mid",
            branch="ticket/mid",
            lead_restarts=_CONFIG.max_lead_restarts - 1,
        ).model_copy(update={"lead_session_id": "dead-lead"})
    )

    dead = {d.ticket_id for d in mgr.reconcile(_now()).dead_leads}

    assert dead == {"mid"}


def test_reset_lead_restarts_zeroes_the_restart_budget(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    mgr = _init(storage)
    # A ticket blocked after its lead kept dying; a human fixes the cause and retries.
    storage.create_manager_ticket(
        mk(S.BLOCKED, "b", attempts=1, lead_restarts=3, branch="ticket/b")
    )

    mgr.update_ticket("b", TicketUpdateRequest(reset_lead_restarts=True))

    t = storage.get_manager_ticket("b")
    assert t is not None
    assert t.lead_restarts == 0
    assert t.attempts == 1  # the delegate spawn budget is untouched


def test_reconcile_latency_measured_from_gate_item(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    mgr = _init(storage)
    # The ticket entered the awaiting state long ago, but its gate item was posted
    # recently (a re-opened or human-deleted-then-reposted gate).
    item = storage.create_inbox_item(
        "mgr", None, "ticket-g: t — spec review", [InboxMarkdownBlockInput(text="x")]
    )
    storage.create_manager_ticket(
        mk(S.SPEC_REVIEW, "g").model_copy(
            update={
                "awaiting_since": item.created_at - timedelta(hours=500),
                "inbox_item_id": item.id,
            }
        )
    )

    # Within the wait measured from the item — not a timeout despite the old entry.
    assert not mgr.reconcile(item.created_at + timedelta(hours=1)).latency_timeouts
    # Past the threshold from the item — a timeout, measured from the item.
    late = mgr.reconcile(item.created_at + timedelta(hours=100)).latency_timeouts
    assert [t.ticket_id for t in late] == ["g"]
    assert late[0].waiting_since == item.created_at


def test_reconcile_latency_skips_resolved_gate(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    mgr = _init(storage)
    # A merge gate the human already answered: the item is resolved, so the wait falls
    # to the external merge and no latency timeout applies.
    answered = storage.create_inbox_item(
        "mgr", None, "ticket-a: t — PR review", [InboxMarkdownBlockInput(text="x")]
    )
    resolved = storage.mark_inbox_read(answered.id)  # a no-action item resolves on read
    assert resolved is not None and resolved[0].status == InboxStatus.RESOLVED
    storage.create_manager_ticket(
        mk(S.REVIEW_REQUESTED, "a").model_copy(
            update={
                "awaiting_since": answered.created_at - timedelta(hours=500),
                "inbox_item_id": answered.id,
            }
        )
    )
    # A control ticket whose gate is still open, equally old.
    open_item = storage.create_inbox_item(
        "mgr", None, "ticket-b: t — PR review", [InboxMarkdownBlockInput(text="x")]
    )
    storage.create_manager_ticket(
        mk(S.REVIEW_REQUESTED, "b").model_copy(update={"inbox_item_id": open_item.id})
    )

    late = mgr.reconcile(open_item.created_at + timedelta(hours=100)).latency_timeouts

    assert [t.ticket_id for t in late] == ["b"]


def test_reconcile_reports_resolved_gate_for_a_deferred_transition(
    tmp_path: Path,
) -> None:
    storage = _storage(tmp_path)
    mgr = _init(storage)
    # An awaiting ticket whose gate the human answered (resolved) but whose transition
    # is deferred — surfaced so the drain re-drives the gate handler.
    answered = storage.create_inbox_item(
        "mgr", None, "ticket-r: t — spec review", [InboxMarkdownBlockInput(text="x")]
    )
    resolved = storage.mark_inbox_read(answered.id)  # a no-action item resolves on read
    assert resolved is not None and resolved[0].status == InboxStatus.RESOLVED
    storage.create_manager_ticket(
        mk(S.SPEC_REVIEW, "r").model_copy(update={"inbox_item_id": answered.id})
    )
    # An awaiting ticket with an OPEN gate is not a resolved_gate (nothing to re-drive).
    open_item = storage.create_inbox_item(
        "mgr", None, "ticket-o: t — spec review", [InboxMarkdownBlockInput(text="x")]
    )
    storage.create_manager_ticket(
        mk(S.SPEC_REVIEW, "o").model_copy(update={"inbox_item_id": open_item.id})
    )

    report = mgr.reconcile(_now())
    assert [g.ticket_id for g in report.resolved_gates] == ["r"]


def test_reconcile_latency_floors_zero_hour_config(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    mgr = _init(storage, ManagerConfig(human_latency_hours=0))
    item = storage.create_inbox_item(
        "mgr", None, "ticket-z: t — spec review", [InboxMarkdownBlockInput(text="x")]
    )
    storage.create_manager_ticket(
        mk(S.SPEC_REVIEW, "z").model_copy(update={"inbox_item_id": item.id})
    )
    # A 0-hour config floors to 1 hour: a fresh gate is not an instant timeout.
    assert not mgr.reconcile(item.created_at + timedelta(minutes=30)).latency_timeouts
    # Past the 1-hour floor it times out.
    late = mgr.reconcile(item.created_at + timedelta(hours=2)).latency_timeouts
    assert [t.ticket_id for t in late] == ["z"]


# ── Storage CRUD ────────────────────────────────────────────────────────────


def test_storage_scopes_tickets_by_manager(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage.create_manager_ticket(mk(S.INTAKE, "a", manager_id="mgr-1"))
    storage.create_manager_ticket(mk(S.INTAKE, "b", manager_id="mgr-2"))
    assert [t.id for t in storage.list_manager_tickets(manager_id="mgr-1")] == ["a"]
    assert [t.id for t in storage.list_manager_tickets(manager_id="mgr-2")] == ["b"]
    # A cross-manager fetch/delete does not reach the other manager's ticket.
    assert storage.get_manager_ticket("a", manager_id="mgr-2") is None
    assert storage.delete_manager_ticket("a", manager_id="mgr-2") is False
    assert storage.get_manager_ticket("a", manager_id="mgr-1") is not None


def test_storage_migrates_legacy_singleton(tmp_path: Path) -> None:
    # Build a pre-multi-manager DB by hand: the old pinned-id config table and a
    # tickets table with no manager_id column, then open it through Storage and
    # assert the migration adopts the one manager under a minted id.
    db = tmp_path / "waypoint.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE manager_tickets (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, priority TEXT NOT NULL,
            state TEXT NOT NULL, scale TEXT, version INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE manager_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            payload TEXT NOT NULL DEFAULT '{}'
        );
        """)
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO manager_config (id, payload) VALUES (1, ?)",
        (
            json.dumps(
                {"trunk": "develop", "project": "legacy", "owner_session_id": "o"}
            ),
        ),
    )
    for tid in ("t1", "t2"):
        payload = {
            "id": tid,
            "title": tid,
            "state": "intake",
            "priority": "p2",
            "created_at": now,
            "updated_at": now,
        }
        conn.execute(
            "INSERT INTO manager_tickets (id, title, priority, state, created_at, "
            "updated_at, payload) VALUES (?, ?, 'p2', 'intake', ?, ?, ?)",
            (tid, tid, now, now, json.dumps(payload)),
        )
    conn.commit()
    conn.close()

    storage = Storage(db)
    configs = storage.list_manager_configs()
    assert len(configs) == 1
    migrated = configs[0]
    assert migrated.id.startswith("mgr-")
    assert migrated.trunk == "develop"
    assert migrated.project == "legacy"
    assert migrated.repo_dir == ""  # unknown until the next init
    # Every legacy ticket is backfilled onto the migrated manager and visible.
    tickets = storage.list_manager_tickets(manager_id=migrated.id)
    assert {t.id for t in tickets} == {"t1", "t2"}
    assert all(t.manager_id == migrated.id for t in tickets)


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


# ── Gate inbox-item id: recorded, cleared per episode ───────────────────────


def test_update_records_inbox_item_id(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    ticket = mgr.create_ticket(TicketCreateRequest(title="x"))
    updated = mgr.update_ticket(ticket.id, TicketUpdateRequest(inbox_item_id="itm-a"))
    assert updated.inbox_item_id == "itm-a"
    assert mgr.get_ticket(ticket.id).inbox_item_id == "itm-a"


def test_transition_clears_inbox_item_id_on_episode_change() -> None:
    # A non-self transition ends the gate episode, so the recorded item clears.
    blocked = mk(S.BLOCKED, branch="ticket/t").model_copy(
        update={"inbox_item_id": "itm-a"}
    )
    resumed = apply_transition(
        blocked, TicketTransitionRequest(to=S.BUILDING), _CONFIG, _now()
    )
    assert resumed.inbox_item_id is None


def test_review_requested_to_blocked_clears_inbox_item_id() -> None:
    # The one awaiting -> awaiting non-self edge: the review-gate item clears so a
    # subsequent blocker gate records its own.
    review = mk(S.REVIEW_REQUESTED, branch="ticket/t").model_copy(
        update={"inbox_item_id": "itm-pr"}
    )
    blocked = apply_transition(
        review, TicketTransitionRequest(to=S.BLOCKED), _CONFIG, _now()
    )
    assert blocked.inbox_item_id is None


def test_self_loop_preserves_inbox_item_id() -> None:
    # The lead-died resume self-loop keeps the still-open gate item.
    blocked = mk(S.BLOCKED, branch="ticket/t").model_copy(
        update={"inbox_item_id": "itm-a"}
    )
    resumed = apply_transition(
        blocked,
        TicketTransitionRequest(to=S.BLOCKED, reason="lead-died"),
        _CONFIG,
        _now(),
    )
    assert resumed.inbox_item_id == "itm-a"
    assert resumed.lead_restarts == 1


def test_multi_gate_episode_id_is_current_not_stale() -> None:
    # The stale-prior-gate scenario at the state-machine layer: an id recorded at
    # the spec gate does not survive into a later blocker gate.
    spec = mk(S.SPEC_REVIEW, scale=Sc.SUBSTANTIAL).model_copy(
        update={"inbox_item_id": "itm-A"}
    )
    ready = apply_transition(spec, TicketTransitionRequest(to=S.READY), _CONFIG, _now())
    assert ready.inbox_item_id is None  # spec-gate exit cleared it
    delegated = apply_transition(
        ready,
        TicketTransitionRequest(to=S.DELEGATED, intended_lead_title="l"),
        _CONFIG,
        _now(),
    )
    building = apply_transition(
        delegated, TicketTransitionRequest(to=S.BUILDING), _CONFIG, _now()
    )
    blocked = apply_transition(
        building, TicketTransitionRequest(to=S.BLOCKED), _CONFIG, _now()
    )
    # Reaching the blocker gate, the stale spec-gate id is gone; only the blocker's
    # own post (not modeled here) would record a fresh one.
    assert blocked.inbox_item_id is None


def test_budget_exhausted_self_loop_preserves_id() -> None:
    # A past-budget self-loop raises before returning, so the id is never cleared.
    blocked = mk(
        S.BLOCKED, lead_restarts=_CONFIG.max_lead_restarts, branch="ticket/t"
    ).model_copy(update={"inbox_item_id": "itm-a"})
    with pytest.raises(ManagerStateError):
        apply_transition(
            blocked,
            TicketTransitionRequest(to=S.BLOCKED, reason="lead-died"),
            _CONFIG,
            _now(),
        )
    assert blocked.inbox_item_id == "itm-a"  # unchanged — apply_transition raised


# ── Config init ─────────────────────────────────────────────────────────────


def test_config_defaults_when_uninitialized(tmp_path: Path) -> None:
    mgr = ManagerManager(_storage(tmp_path), "nope")
    config = mgr.config()
    assert config.trunk == "main"  # ManagerConfig default
    assert config.max_delegate_attempts == 3


def test_init_persists_config(tmp_path: Path) -> None:
    mgr = _init(
        _storage(tmp_path),
        ManagerConfig(max_delegate_attempts=5, trunk="develop"),
    )
    config = mgr.config()
    assert config.max_delegate_attempts == 5
    assert config.trunk == "develop"


def test_init_mints_id_and_keys_by_repo(tmp_path: Path) -> None:
    reg = ManagerRegistry(_storage(tmp_path))
    a = reg.init(ManagerInitRequest(config=ManagerConfig(repo_dir="/repo/a")))
    assert a.id.startswith("mgr-")
    # Re-init from the same repo reuses the id (idempotent).
    a2 = reg.init(ManagerInitRequest(config=ManagerConfig(repo_dir="/repo/a")))
    assert a2.id == a.id
    # A different repo mints a distinct manager.
    b = reg.init(ManagerInitRequest(config=ManagerConfig(repo_dir="/repo/b")))
    assert b.id != a.id
    assert {m.id for m in reg.list_summaries()} == {a.id, b.id}


def test_init_rejects_channel_collision(tmp_path: Path) -> None:
    reg = ManagerRegistry(_storage(tmp_path))
    rc = ManagerRenderContext(tickets_channel="tickets", ticket_channel_prefix="tk-")
    reg.init(
        ManagerInitRequest(config=ManagerConfig(repo_dir="/repo/a", render_context=rc))
    )
    with pytest.raises(HTTPException) as exc:
        reg.init(
            ManagerInitRequest(
                config=ManagerConfig(repo_dir="/repo/b", render_context=rc)
            )
        )
    assert exc.value.status_code == 409


# ── deinit / delete / owner cascade ─────────────────────────────────────────


def test_deinit_clears_tickets_and_config(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.create_ticket(TicketCreateRequest(title="a"))
    mgr.create_ticket(TicketCreateRequest(title="b"))
    assert mgr.deinit() == 2
    state = mgr.state()
    assert state.tickets == []
    assert state.config is None


def test_delete_ticket(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    ticket = mgr.create_ticket(TicketCreateRequest(title="a"))
    mgr.delete_ticket(ticket.id)
    assert mgr.list_tickets() == []


def test_delete_ticket_404(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    with pytest.raises(HTTPException) as exc:
        mgr.delete_ticket("nope")
    assert exc.value.status_code == 404


def test_deinit_owned_by_cascades_only_the_owned_managers(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    reg = ManagerRegistry(storage)
    a = reg.init(
        ManagerInitRequest(
            config=ManagerConfig(repo_dir="/repo/a", owner_session_id="mgr")
        )
    )
    b = reg.init(
        ManagerInitRequest(
            config=ManagerConfig(repo_dir="/repo/b", owner_session_id="other")
        )
    )
    reg.get(a.id).create_ticket(TicketCreateRequest(title="a"))
    reg.get(b.id).create_ticket(TicketCreateRequest(title="b"))
    # Deleting a non-owner session cascades nothing.
    assert reg.deinit_owned_by("stranger") == 0
    # Deleting the owner tears down only its manager; the other survives.
    assert reg.deinit_owned_by("mgr") == 1
    assert reg.get(a.id).list_tickets() == []
    assert reg.exists(a.id) is False
    assert len(reg.get(b.id).list_tickets()) == 1
    assert reg.exists(b.id) is True


def test_init_preserves_owner_when_not_resupplied(tmp_path: Path) -> None:
    reg = ManagerRegistry(_storage(tmp_path))
    first = reg.init(
        ManagerInitRequest(
            config=ManagerConfig(repo_dir="/repo/a", owner_session_id="mgr")
        )
    )
    # Re-init from a manifest (which carries no owner) keeps the recorded owner.
    reg.init(
        ManagerInitRequest(config=ManagerConfig(repo_dir="/repo/a", trunk="develop"))
    )
    config = reg.get(first.id).config()
    assert config.owner_session_id == "mgr"
    assert config.trunk == "develop"
    # An explicit new owner overrides.
    reg.init(
        ManagerInitRequest(
            config=ManagerConfig(repo_dir="/repo/a", owner_session_id="mgr2")
        )
    )
    assert reg.get(first.id).config().owner_session_id == "mgr2"
