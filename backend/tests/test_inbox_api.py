"""Route-level tests for the inbox endpoints over the real FastAPI app."""

from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from waypoint.api import create_app
from waypoint.settings import Settings


def _build(tmp_path: Path) -> tuple[Any, str]:
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    context = app.state.context
    token = context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _question_block() -> dict[str, Any]:
    return {
        "type": "question",
        "question": "Ship it?",
        "options": [{"label": "yes"}, {"label": "no"}],
        "required": True,
    }


async def test_requires_auth(tmp_path: Path) -> None:
    app, _ = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/inbox")
    assert resp.status_code == 401


async def test_post_and_get_round_trip(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={
                "subject": "PRD ready",
                "from_session_id": "s1",
                "blocks": [
                    {"type": "markdown", "text": "# summary"},
                    _question_block(),
                ],
            },
            headers=_auth(token),
        )
        assert post.status_code == 200
        item = post.json()["item"]
        assert item["status"] == "open"
        assert item["version"] == 0
        block_ids = [b["id"] for b in item["blocks"]]
        assert all(block_ids)

        got = await client.get(f"/api/inbox/{item['id']}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["item"]["subject"] == "PRD ready"


async def test_get_missing_is_404(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/inbox/ghost", headers=_auth(token))
    assert resp.status_code == 404


async def test_block_submit_resolves_and_bumps_version(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={"subject": "gate", "blocks": [_question_block()]},
            headers=_auth(token),
        )
        item = post.json()["item"]
        bid = item["blocks"][0]["id"]
        resp = await client.post(
            f"/api/inbox/{item['id']}/blocks/{bid}",
            json={"answer": {"selected": ["yes"]}, "reply": {"notes": "lgtm"}},
            headers=_auth(token),
        )
    assert resp.status_code == 200
    updated = resp.json()["item"]
    assert updated["status"] == "resolved"
    assert updated["version"] == 1
    assert updated["blocks"][0]["answer"]["selected"] == ["yes"]
    assert updated["blocks"][0]["reply"]["notes"] == "lgtm"


async def test_block_submit_type_mismatch_is_422(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={"subject": "m", "blocks": [{"type": "markdown", "text": "hi"}]},
            headers=_auth(token),
        )
        item = post.json()["item"]
        bid = item["blocks"][0]["id"]
        resp = await client.post(
            f"/api/inbox/{item['id']}/blocks/{bid}",
            json={"answer": {"selected": ["yes"]}},
            headers=_auth(token),
        )
    assert resp.status_code == 422


async def test_block_submit_missing_block_is_404(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={"subject": "gate", "blocks": [_question_block()]},
            headers=_auth(token),
        )
        item = post.json()["item"]
        resp = await client.post(
            f"/api/inbox/{item['id']}/blocks/nope",
            json={"answer": {"selected": ["yes"]}},
            headers=_auth(token),
        )
    assert resp.status_code == 404


async def test_read_resolves_no_action_item(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={"subject": "fyi", "blocks": [{"type": "markdown", "text": "hi"}]},
            headers=_auth(token),
        )
        item = post.json()["item"]
        resp = await client.post(f"/api/inbox/{item['id']}/read", headers=_auth(token))
    assert resp.status_code == 200
    read = resp.json()["item"]
    assert read["status"] == "resolved"
    assert read["read_at"] is not None


async def test_list_filter_search_and_unresolved_count(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        for i in range(3):
            await client.post(
                "/api/inbox",
                json={"subject": f"item {i}", "blocks": [_question_block()]},
                headers=_auth(token),
            )
        count = await client.get("/api/inbox/unresolved-count", headers=_auth(token))
        assert count.json()["unresolved_count"] == 3

        page = await client.get(
            "/api/inbox", params={"status": "open", "limit": 2}, headers=_auth(token)
        )
        body = page.json()
        assert len(body["items"]) == 2
        assert body["has_more"] is True
        assert body["cursor"]

        page2 = await client.get(
            "/api/inbox",
            params={"status": "open", "limit": 2, "cursor": body["cursor"]},
            headers=_auth(token),
        )
        assert len(page2.json()["items"]) == 1

        search = await client.get(
            "/api/inbox", params={"q": "item 1"}, headers=_auth(token)
        )
    assert [i["subject"] for i in search.json()["items"]] == ["item 1"]


async def test_delete_removes_item(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={"subject": "gate", "blocks": [_question_block()]},
            headers=_auth(token),
        )
        item_id = post.json()["item"]["id"]
        first = await client.delete(f"/api/inbox/{item_id}", headers=_auth(token))
        assert first.status_code == 200
        again = await client.delete(f"/api/inbox/{item_id}", headers=_auth(token))
    assert again.status_code == 404


# ── WebSocket stream (drives ``inbox wait``) ──────────────────────────


def test_ws_hydrates_existing_item(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    with TestClient(app) as client:
        post = client.post(
            "/api/inbox",
            json={"subject": "gate", "blocks": [_question_block()]},
            headers=_auth(token),
        )
        item = post.json()["item"]
        with client.websocket_connect(f"/ws/inbox/{item['id']}?token={token}") as ws:
            frame = ws.receive_json()
    assert frame["type"] == "inbox_update"
    assert frame["payload"]["deleted"] is False
    assert frame["payload"]["item"]["id"] == item["id"]


def test_ws_already_gone_emits_deleted(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/inbox/ghost?token={token}") as ws:
            frame = ws.receive_json()
    assert frame["type"] == "inbox_update"
    assert frame["payload"]["deleted"] is True
    assert frame["payload"]["item"] is None


def test_ws_pushes_live_update(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    with TestClient(app) as client:
        post = client.post(
            "/api/inbox",
            json={"subject": "gate", "blocks": [_question_block()]},
            headers=_auth(token),
        )
        item = post.json()["item"]
        bid = item["blocks"][0]["id"]
        with client.websocket_connect(f"/ws/inbox/{item['id']}?token={token}") as ws:
            ws.receive_json()  # hydration frame
            client.post(
                f"/api/inbox/{item['id']}/blocks/{bid}",
                json={"answer": {"selected": ["yes"]}},
                headers=_auth(token),
            )
            live = ws.receive_json()
    assert live["type"] == "inbox_update"
    assert live["payload"]["item"]["status"] == "resolved"
    assert live["payload"]["item"]["version"] == 1
    assert live["payload"]["unresolved_count"] == 0


def test_ws_rejects_bad_token(tmp_path: Path) -> None:
    app, _ = _build(tmp_path)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/inbox/x?token=bad") as ws:
                ws.receive_json()
