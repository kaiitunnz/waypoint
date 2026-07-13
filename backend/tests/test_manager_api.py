"""Route-level tests for the Waypoint Manager and wake-subscription endpoints
over the real FastAPI app (in-process ASGI, no live server). These close the
CLI↔client↔API↔runtime seam that the pure-logic tests in test_manager.py and
test_wake.py do not exercise."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from waypoint.api import create_app
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings


def _build(tmp_path: Path) -> tuple[Any, str]:
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    token = app.state.context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_session(settings: Settings, session_id: str) -> SessionRecord:
    # Fabricate a persisted session row the way tests/test_wake.py does, so a
    # wake subscription has a real session to attach to.
    session_dir = settings.sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    return SessionRecord(
        id=session_id,
        backend="codex",
        source=SessionSource.MANAGED,
        transport="codex_app_server",
        title="Subscriber",
        cwd="/tmp/project",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
    )


async def _create(client: httpx.AsyncClient, token: str, **body: Any) -> str:
    resp = await client.post("/api/manager/tickets", headers=_auth(token), json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["ticket"]["id"]


async def _transition(
    client: httpx.AsyncClient, token: str, ticket_id: str, to: str, **meta: Any
) -> httpx.Response:
    return await client.post(
        f"/api/manager/tickets/{ticket_id}/transition",
        headers=_auth(token),
        json={"to": to, **meta},
    )


# ── Manager API ───────────────────────────────────────────────────────────


async def test_manager_requires_auth(tmp_path: Path) -> None:
    app, _ = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/manager/state")
    assert resp.status_code == 401


async def test_init_sets_config(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/manager/init",
            headers=_auth(token),
            json={"config": {"execution_slots": 4, "trunk": "develop"}},
        )
        assert resp.status_code == 200
        config = resp.json()["config"]
        assert config["execution_slots"] == 4
        assert config["trunk"] == "develop"
        # The persisted config is reflected back through /state.
        state = await client.get("/api/manager/state", headers=_auth(token))
    assert state.json()["config"]["execution_slots"] == 4


async def test_create_ticket_starts_in_intake(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/manager/tickets",
            headers=_auth(token),
            json={"title": "ship it", "priority": "p1"},
        )
        assert resp.status_code == 200
        ticket = resp.json()["ticket"]
    assert ticket["state"] == "intake"
    assert ticket["title"] == "ship it"
    assert ticket["priority"] == "p1"


async def test_next_recommends_triage_for_fresh_intake(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        ticket_id = await _create(client, token, title="fresh")
        resp = await client.get("/api/manager/next", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
    # Envelope shape the skill's re-anchor depends on: slots + per-ticket legal
    # transitions + a single recommended pull move.
    assert set(body["slots"]) == {"total", "used", "free"}
    assert body["slots"]["free"] == 1  # default execution_slots (single shared tree)
    (entry,) = body["tickets"]
    assert entry["ticket_id"] == ticket_id
    assert entry["legal_transitions"] == ["triaged"]
    rec = body["recommended"]
    assert rec["ticket_id"] == ticket_id
    assert rec["from_state"] == "intake"
    assert rec["to_state"] == "triaged"
    assert rec["event"] == "triage"


async def test_legal_transition_walk_to_merged(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        ticket_id = await _create(client, token, title="walk", scale="trivial")
        walk = [
            "triaged",
            "ready",
            "delegated",
            "building",
            "review_requested",
            "merging",
            "merged",
        ]
        for to in walk:
            resp = await _transition(client, token, ticket_id, to)
            assert resp.status_code == 200, (to, resp.text)
            assert resp.json()["ticket"]["state"] == to


async def test_illegal_transition_is_409(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        ticket_id = await _create(client, token, title="x")
        # intake -> merging is not on the transition table.
        resp = await _transition(client, token, ticket_id, "merging")
    assert resp.status_code == 409


async def test_invariant_slot_cap_is_409(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        await client.post(
            "/api/manager/init",
            headers=_auth(token),
            json={"config": {"execution_slots": 1}},
        )
        a = await _create(client, token, title="a", scale="trivial")
        for to in ("triaged", "ready", "delegated", "building"):
            meta = {"intended_lead_title": "a-lead"} if to == "delegated" else {}
            assert (await _transition(client, token, a, to, **meta)).status_code == 200
        # B reaches ready without a slot; delegating it would put two tickets on
        # the shared tree with the cap at 1 -> the second delegate is a 409.
        b = await _create(client, token, title="b", scale="trivial")
        for to in ("triaged", "ready"):
            assert (await _transition(client, token, b, to)).status_code == 200
        resp = await _transition(
            client, token, b, "delegated", intended_lead_title="b-lead"
        )
    assert resp.status_code == 409


async def test_invariant_second_spec_pending_is_409(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        ids = []
        for name in ("a", "b"):
            tid = await _create(client, token, title=name, scale="substantial")
            assert (
                await _transition(client, token, tid, "triaged", scale="substantial")
            ).status_code == 200
            ids.append(tid)
        assert (
            await _transition(client, token, ids[0], "spec_pending")
        ).status_code == 200
        # Serial analysis: at most one ticket may be in spec_pending.
        resp = await _transition(client, token, ids[1], "spec_pending")
    assert resp.status_code == 409


async def test_invariant_duplicate_lead_title_is_409(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        await client.post(
            "/api/manager/init",
            headers=_auth(token),
            json={"config": {"execution_slots": 5}},
        )
        ids = []
        for name in ("a", "b"):
            tid = await _create(client, token, title=name, scale="trivial")
            for to in ("triaged", "ready"):
                assert (await _transition(client, token, tid, to)).status_code == 200
            ids.append(tid)
        assert (
            await _transition(
                client, token, ids[0], "delegated", intended_lead_title="shared"
            )
        ).status_code == 200
        # The spawn-dedup key must be unique across live tickets.
        resp = await _transition(
            client, token, ids[1], "delegated", intended_lead_title="shared"
        )
    assert resp.status_code == 409


async def test_state_reports_config_slots_tickets_lock(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        await client.post(
            "/api/manager/init",
            headers=_auth(token),
            json={"config": {"execution_slots": 3}},
        )
        ticket_id = await _create(client, token, title="t")
        resp = await client.get("/api/manager/state", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
    assert body["config"]["execution_slots"] == 3
    assert set(body["slots"]) == {"total", "used", "free"}
    assert [t["id"] for t in body["tickets"]] == [ticket_id]
    assert body["lock"] is None


async def test_lock_acquire_conflict_steal_release(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        acquire = await client.post(
            "/api/manager/lock", headers=_auth(token), json={"owner": "m1"}
        )
        assert acquire.status_code == 200
        assert acquire.json()["lock"]["owner"] == "m1"
        # A different owner cannot acquire the held lease.
        conflict = await client.post(
            "/api/manager/lock", headers=_auth(token), json={"owner": "m2"}
        )
        assert conflict.status_code == 409
        # Nor steal it before the TTL expires.
        steal = await client.post(
            "/api/manager/lock/steal", headers=_auth(token), json={"owner": "m2"}
        )
        assert steal.status_code == 409
        # The current owner releases it (DELETE carries a body).
        release = await client.request(
            "DELETE", "/api/manager/lock", headers=_auth(token), json={"owner": "m1"}
        )
        assert release.status_code == 200
        assert release.json() == {"released": True}
        # Freed: another owner can now acquire.
        reacquire = await client.post(
            "/api/manager/lock", headers=_auth(token), json={"owner": "m2"}
        )
    assert reacquire.status_code == 200
    assert reacquire.json()["lock"]["owner"] == "m2"


# ── Wake subscriptions ──────────────────────────────────────────────────────


async def test_wake_register_unknown_session_is_404(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/sessions/ghost/wake-subscriptions",
            headers=_auth(token),
            json={"channel_globs": ["tickets"]},
        )
    assert resp.status_code == 404


async def test_wake_register_list_delete_round_trip(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    context = app.state.context
    context.storage.create_session(_make_session(context.settings, "codex-sub"))
    async with _client(app) as client:
        register = await client.post(
            "/api/sessions/codex-sub/wake-subscriptions",
            headers=_auth(token),
            json={"channel_globs": ["ticket-*"], "wake_on_inbox": True},
        )
        assert register.status_code == 200
        sub = register.json()["subscription"]
        assert sub["session_id"] == "codex-sub"
        assert sub["channel_globs"] == ["ticket-*"]
        assert sub["wake_on_inbox"] is True
        # It is persisted and listable.
        listing = await client.get(
            "/api/sessions/codex-sub/wake-subscriptions", headers=_auth(token)
        )
        assert [s["id"] for s in listing.json()["subscriptions"]] == [sub["id"]]
        # Delete it; the list is then empty.
        deleted = await client.delete(
            f"/api/sessions/codex-sub/wake-subscriptions/{sub['id']}",
            headers=_auth(token),
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}
        empty = await client.get(
            "/api/sessions/codex-sub/wake-subscriptions", headers=_auth(token)
        )
    assert empty.json()["subscriptions"] == []


async def test_deinit_endpoint_clears_state(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        await _create(client, token, title="a")
        resp = await client.delete("/api/manager", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["deinitialized"] is True
        assert body["tickets_deleted"] == 1
        state = await client.get("/api/manager/state", headers=_auth(token))
    assert state.json()["tickets"] == []


async def test_delete_ticket_endpoint_and_404(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        tid = await _create(client, token, title="a")
        ok = await client.delete(f"/api/manager/tickets/{tid}", headers=_auth(token))
        assert ok.status_code == 200
        assert ok.json()["deleted"] is True
        missing = await client.delete(
            f"/api/manager/tickets/{tid}", headers=_auth(token)
        )
    assert missing.status_code == 404
