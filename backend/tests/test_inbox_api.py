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


async def test_attachment_block_ref_is_denormalized(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    spec = app.state.context.runtime.attachments.save(
        "s1", data=b"\x89PNG\r\n", filename="shot.png", content_type="image/png"
    )
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={
                "subject": "with attachment",
                "from_session_id": "s1",
                "blocks": [
                    {
                        "type": "attachment",
                        "ref": {"session_id": "s1", "attachment_id": spec.id},
                    }
                ],
            },
            headers=_auth(token),
        )
        assert post.status_code == 200
        ref = post.json()["item"]["blocks"][0]["ref"]
    # The runtime resolved the spec at post time so the UI needs no lookup.
    assert ref["filename"] == spec.filename
    assert ref["kind"] == "image"


async def test_reply_attachment_ref_is_denormalized(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    spec = app.state.context.runtime.attachments.save(
        "s1", data=b"\x89PNG\r\n", filename="reply.png", content_type="image/png"
    )
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={
                "subject": "gate",
                "from_session_id": "s1",
                "blocks": [_question_block()],
            },
            headers=_auth(token),
        )
        item = post.json()["item"]
        block_id = item["blocks"][0]["id"]
        submit = await client.post(
            f"/api/inbox/{item['id']}/blocks/{block_id}",
            json={
                "reply": {
                    "notes": "see file",
                    "attachments": [{"session_id": "s1", "attachment_id": spec.id}],
                }
            },
            headers=_auth(token),
        )
        assert submit.status_code == 200
        reply = submit.json()["item"]["blocks"][0]["reply"]
    assert reply["attachments"][0]["filename"] == spec.filename
    assert reply["attachments"][0]["kind"] == "image"


async def test_unresolvable_reply_ref_has_no_denormalized_name(tmp_path: Path) -> None:
    # A user reply may attach a ref that later can't be resolved; that path still
    # degrades to no label (the backend never trusts a client-supplied name),
    # unlike an outbound post block, which fails closed with a 422.
    app, token = _build(tmp_path)
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={"subject": "gate", "blocks": [_question_block()]},
            headers=_auth(token),
        )
        item = post.json()["item"]
        block_id = item["blocks"][0]["id"]
        submit = await client.post(
            f"/api/inbox/{item['id']}/blocks/{block_id}",
            json={
                "reply": {
                    "attachments": [{"session_id": "s1", "attachment_id": "0" * 32}]
                }
            },
            headers=_auth(token),
        )
        assert submit.status_code == 200
        ref = submit.json()["item"]["blocks"][0]["reply"]["attachments"][0]
    assert ref["filename"] is None
    assert ref["kind"] is None


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


async def _post_item(
    client: httpx.AsyncClient, token: str, subject: str
) -> dict[str, Any]:
    resp = await client.post(
        "/api/inbox",
        json={"subject": subject, "blocks": [_question_block()]},
        headers=_auth(token),
    )
    return resp.json()["item"]


async def _resolve(client: httpx.AsyncClient, token: str, item: dict[str, Any]) -> None:
    block_id = item["blocks"][0]["id"]
    await client.post(
        f"/api/inbox/{item['id']}/blocks/{block_id}",
        json={"answer": {"selected": ["yes"]}},
        headers=_auth(token),
    )


async def test_batch_delete_removes_known_ids(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        a = await _post_item(client, token, "a")
        b = await _post_item(client, token, "b")
        c = await _post_item(client, token, "c")

        resp = await client.post(
            "/api/inbox/batch-delete",
            json={"item_ids": [a["id"], c["id"], "ghost"]},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["deleted_ids"]) == {a["id"], c["id"]}
        assert body["count"] == 2

        assert (
            await client.get(f"/api/inbox/{a['id']}", headers=_auth(token))
        ).status_code == 404
        assert (
            await client.get(f"/api/inbox/{b['id']}", headers=_auth(token))
        ).status_code == 200


async def test_batch_delete_empty_list(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        await _post_item(client, token, "a")
        resp = await client.post(
            "/api/inbox/batch-delete", json={"item_ids": []}, headers=_auth(token)
        )
        assert resp.status_code == 200
        assert resp.json() == {"deleted_ids": [], "count": 0}
        count = await client.get("/api/inbox/unresolved-count", headers=_auth(token))
    assert count.json()["unresolved_count"] == 1


async def test_delete_resolved_leaves_open(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        open_item = await _post_item(client, token, "open")
        res_a = await _post_item(client, token, "res-a")
        res_b = await _post_item(client, token, "res-b")
        await _resolve(client, token, res_a)
        await _resolve(client, token, res_b)

        resp = await client.post("/api/inbox/delete-resolved", headers=_auth(token))
        assert resp.status_code == 200
        assert set(resp.json()["deleted_ids"]) == {res_a["id"], res_b["id"]}

        assert (
            await client.get(f"/api/inbox/{open_item['id']}", headers=_auth(token))
        ).status_code == 200
        # Idempotent once the resolved folder is empty.
        again = await client.post("/api/inbox/delete-resolved", headers=_auth(token))
    assert again.json() == {"deleted_ids": [], "count": 0}


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


# ── attachment reference indexing (RFC: manager inbox attachments) ──


def _approval_block() -> dict[str, Any]:
    return {
        "type": "approval",
        "prompt": "Approve this spec?",
        "options": ["approve", "reject"],
        "required": True,
    }


def _seed_attachment(app: Any, session_id: str) -> str:
    """Store a blob directly (bypassing the session-gated HTTP upload) and return
    its id."""
    spec = app.state.context.runtime.attachments.save(
        session_id, data=b"# rfc", filename="rfc.md", content_type="text/markdown"
    )
    return spec.id


async def test_post_with_attachment_indexes_and_denormalizes(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    store = app.state.context.runtime.attachments
    aid = _seed_attachment(app, "mgr")
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={
                "subject": "chan: t — spec review",
                "from_session_id": "mgr",
                "blocks": [
                    {"type": "markdown", "text": "review"},
                    {
                        "type": "attachment",
                        "ref": {"session_id": "mgr", "attachment_id": aid},
                    },
                    _approval_block(),
                ],
            },
            headers=_auth(token),
        )
    assert post.status_code == 200, post.text
    item = post.json()["item"]
    att = next(b for b in item["blocks"] if b["type"] == "attachment")
    # Runtime denormalized the display name/kind from the resolved spec.
    assert att["ref"]["filename"] == "rfc.md"
    assert att["ref"]["kind"] == "file"
    # And pinned it against this item so the orphan sweep keeps it.
    assert store.inbox_referenced_ids("mgr") == {aid}


async def test_post_with_unresolvable_attachment_is_422_and_creates_nothing(
    tmp_path: Path,
) -> None:
    app, token = _build(tmp_path)
    store = app.state.context.runtime.attachments
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={
                "subject": "bad ref",
                "from_session_id": "mgr",
                "blocks": [
                    {
                        "type": "attachment",
                        "ref": {"session_id": "mgr", "attachment_id": "f" * 32},
                    },
                    _approval_block(),
                ],
            },
            headers=_auth(token),
        )
        assert post.status_code == 422
        listing = await client.get("/api/inbox", headers=_auth(token))
    # No item persisted, no membership left behind.
    assert listing.json()["items"] == []
    assert store.inbox_referenced_ids("mgr") == set()


async def test_delete_item_releases_attachment_ref(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    store = app.state.context.runtime.attachments
    aid = _seed_attachment(app, "mgr")
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={
                "subject": "chan: t — spec review",
                "from_session_id": "mgr",
                "blocks": [
                    {
                        "type": "attachment",
                        "ref": {"session_id": "mgr", "attachment_id": aid},
                    },
                    _approval_block(),
                ],
            },
            headers=_auth(token),
        )
        item_id = post.json()["item"]["id"]
        assert store.inbox_referenced_ids("mgr") == {aid}
        await client.delete(f"/api/inbox/{item_id}", headers=_auth(token))
    # Deleting the item releases the pin; the upload is sweep-eligible again.
    assert store.inbox_referenced_ids("mgr") == set()


async def test_delete_resolved_releases_attachment_refs(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    store = app.state.context.runtime.attachments
    aid = _seed_attachment(app, "mgr")
    async with _client(app) as client:
        post = await client.post(
            "/api/inbox",
            json={
                "subject": "chan: t — spec review",
                "from_session_id": "mgr",
                "blocks": [
                    {
                        "type": "attachment",
                        "ref": {"session_id": "mgr", "attachment_id": aid},
                    },
                    _approval_block(),
                ],
            },
            headers=_auth(token),
        )
        item = post.json()["item"]
        block_id = next(b["id"] for b in item["blocks"] if b["type"] == "approval")
        await client.post(
            f"/api/inbox/{item['id']}/blocks/{block_id}",
            json={"answer": {"decision": "approve"}},
            headers=_auth(token),
        )
        assert store.inbox_referenced_ids("mgr") == {aid}
        resp = await client.post("/api/inbox/delete-resolved", headers=_auth(token))
        assert resp.status_code == 200
    assert store.inbox_referenced_ids("mgr") == set()


async def test_batch_delete_releases_refs_only_for_deleted(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    store = app.state.context.runtime.attachments
    kept_aid = _seed_attachment(app, "mgr")
    gone_aid = _seed_attachment(app, "mgr")

    async def _post(aid: str) -> str:
        resp = await client.post(
            "/api/inbox",
            json={
                "subject": "chan: t — spec review",
                "from_session_id": "mgr",
                "blocks": [
                    {
                        "type": "attachment",
                        "ref": {"session_id": "mgr", "attachment_id": aid},
                    },
                    _approval_block(),
                ],
            },
            headers=_auth(token),
        )
        return resp.json()["item"]["id"]

    async with _client(app) as client:
        kept_id = await _post(kept_aid)
        gone_id = await _post(gone_aid)
        assert store.inbox_referenced_ids("mgr") == {kept_aid, gone_aid}
        resp = await client.post(
            "/api/inbox/batch-delete",
            json={"item_ids": [gone_id, "ghost"]},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert kept_id  # still present
    # Only the deleted item's ref is released; the surviving item keeps its pin.
    assert store.inbox_referenced_ids("mgr") == {kept_aid}
