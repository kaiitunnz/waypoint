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


async def _init(
    client: httpx.AsyncClient, token: str, repo_dir: str = "/repo/x", **config: Any
) -> str:
    resp = await client.post(
        "/api/manager/init",
        headers=_auth(token),
        json={"config": {"repo_dir": repo_dir, **config}},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["config"]["id"]


async def _create(client: httpx.AsyncClient, token: str, mid: str, **body: Any) -> str:
    resp = await client.post(
        f"/api/manager/{mid}/tickets", headers=_auth(token), json=body
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["ticket"]["id"]


async def _transition(
    client: httpx.AsyncClient,
    token: str,
    mid: str,
    ticket_id: str,
    to: str,
    **meta: Any,
) -> httpx.Response:
    return await client.post(
        f"/api/manager/{mid}/tickets/{ticket_id}/transition",
        headers=_auth(token),
        json={"to": to, **meta},
    )


# ── Manager API ───────────────────────────────────────────────────────────


async def test_manager_requires_auth(tmp_path: Path) -> None:
    app, _ = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/manager/x/state")
    assert resp.status_code == 401


async def test_init_sets_config(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.post(
            "/api/manager/init",
            headers=_auth(token),
            json={
                "config": {
                    "repo_dir": "/repo/x",
                    "max_delegate_attempts": 5,
                    "trunk": "develop",
                }
            },
        )
        assert resp.status_code == 200
        config = resp.json()["config"]
        assert config["id"].startswith("mgr-")
        assert config["max_delegate_attempts"] == 5
        assert config["trunk"] == "develop"
        # The persisted config is reflected back through /state.
        state = await client.get(
            f"/api/manager/{config['id']}/state", headers=_auth(token)
        )
    assert state.json()["config"]["max_delegate_attempts"] == 5


async def test_unknown_manager_is_404(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/manager/mgr-nope/state", headers=_auth(token))
    assert resp.status_code == 404


async def test_manager_list_enumerates(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        a = await _init(client, token, repo_dir="/repo/a", project="A")
        b = await _init(client, token, repo_dir="/repo/b", project="B")
        await _create(client, token, a, title="t")
        resp = await client.get("/api/manager", headers=_auth(token))
        assert resp.status_code == 200
        managers = {m["id"]: m for m in resp.json()["managers"]}
    assert set(managers) == {a, b}
    assert managers[a]["project"] == "A"
    assert managers[a]["ticket_count"] == 1
    assert managers[b]["ticket_count"] == 0


async def test_create_ticket_starts_in_intake(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        mid = await _init(client, token)
        resp = await client.post(
            f"/api/manager/{mid}/tickets",
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
        mid = await _init(client, token)
        ticket_id = await _create(client, token, mid, title="fresh")
        resp = await client.get(f"/api/manager/{mid}/next", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
    # Envelope shape the skill's re-anchor depends on: tree state + per-ticket legal
    # transitions + a single recommended pull move.
    assert set(body["tree"]) == {"free", "held_by"}
    assert body["tree"]["free"] is True  # the single shared tree is free
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
        mid = await _init(client, token)
        ticket_id = await _create(client, token, mid, title="walk", scale="trivial")
        walk = [
            "triaged",
            "ready",
            "delegated",
            "building",
            "review_requested",
            "merged",
        ]
        for to in walk:
            resp = await _transition(client, token, mid, ticket_id, to)
            assert resp.status_code == 200, (to, resp.text)
            assert resp.json()["ticket"]["state"] == to


async def test_illegal_transition_is_409(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        mid = await _init(client, token)
        ticket_id = await _create(client, token, mid, title="x")
        # intake -> merged is not on the transition table.
        resp = await _transition(client, token, mid, ticket_id, "merged")
    assert resp.status_code == 409


async def test_invariant_tree_cap_is_409(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        mid = await _init(client, token)
        a = await _create(client, token, mid, title="a", scale="trivial")
        for to in ("triaged", "ready", "delegated", "building"):
            meta = {"intended_lead_title": "a-lead"} if to == "delegated" else {}
            assert (
                await _transition(client, token, mid, a, to, **meta)
            ).status_code == 200
        # B reaches ready while A holds the tree; delegating it would put two
        # tickets on the one shared tree -> the second delegate is a 409.
        b = await _create(client, token, mid, title="b", scale="trivial")
        for to in ("triaged", "ready"):
            assert (await _transition(client, token, mid, b, to)).status_code == 200
        resp = await _transition(
            client, token, mid, b, "delegated", intended_lead_title="b-lead"
        )
    assert resp.status_code == 409


async def test_invariant_second_spec_pending_is_409(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        mid = await _init(client, token)
        ids = []
        for name in ("a", "b"):
            tid = await _create(client, token, mid, title=name, scale="substantial")
            assert (
                await _transition(
                    client, token, mid, tid, "triaged", scale="substantial"
                )
            ).status_code == 200
            ids.append(tid)
        assert (
            await _transition(client, token, mid, ids[0], "spec_pending")
        ).status_code == 200
        # Serial analysis: at most one ticket may be in spec_pending.
        resp = await _transition(client, token, mid, ids[1], "spec_pending")
    assert resp.status_code == 409

    # (The duplicate-intended-lead-title invariant is unreachable through
    # transitions under the single-tree cap — a second ticket can never enter
    # delegated while one holds the tree — so it is covered by the pure
    # test_unique_intended_lead_title_across_live_tickets in test_manager.py.)


async def test_state_reports_config_tree_tickets(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        mid = await _init(client, token, trunk="develop")
        ticket_id = await _create(client, token, mid, title="t")
        resp = await client.get(f"/api/manager/{mid}/state", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
    assert body["config"]["trunk"] == "develop"
    assert set(body["tree"]) == {"free", "held_by"}
    assert [t["id"] for t in body["tickets"]] == [ticket_id]


async def test_reconcile_endpoint_reports_intake(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        mid = await _init(
            client,
            token,
            owner_session_id="mgr",
            render_context={
                "templates_dir": "/x",
                "tickets_channel": "tickets",
                "ticket_channel_prefix": "ticket-",
            },
        )
        await client.post(
            "/api/board/tickets",
            headers=_auth(token),
            json={"text": "please fix X", "author_session_id": "human"},
        )
        resp = await client.get(f"/api/manager/{mid}/reconcile", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
    assert set(body) == {
        "unregistered_intake",
        "dead_leads",
        "latency_timeouts",
        "stale_gates",
        "finalize_pending",
        "resolved_gates",
    }
    assert [i["text"] for i in body["unregistered_intake"]] == ["please fix X"]


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
        mid = await _init(client, token)
        await _create(client, token, mid, title="a")
        resp = await client.delete(f"/api/manager/{mid}", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["deinitialized"] is True
        assert body["tickets_deleted"] == 1
        # The manager is gone; its state route now 404s.
        state = await client.get(f"/api/manager/{mid}/state", headers=_auth(token))
    assert state.status_code == 404


async def test_delete_ticket_endpoint_and_404(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        mid = await _init(client, token)
        tid = await _create(client, token, mid, title="a")
        ok = await client.delete(
            f"/api/manager/{mid}/tickets/{tid}", headers=_auth(token)
        )
        assert ok.status_code == 200
        assert ok.json()["deleted"] is True
        missing = await client.delete(
            f"/api/manager/{mid}/tickets/{tid}", headers=_auth(token)
        )
    assert missing.status_code == 404
