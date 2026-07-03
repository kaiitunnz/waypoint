"""Session tags: the tag-filter matcher plus the list-filter and set-tags
routes over the real FastAPI app via an in-process ASGI transport."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from waypoint.api import create_app, session_matches_tag_filters
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings


def test_matcher_key_value_and_bare_key() -> None:
    tags = {"role": "backend-lead", "overflow": ""}
    assert session_matches_tag_filters(tags, ["role=backend-lead"]) is True
    assert session_matches_tag_filters(tags, ["role=frontend"]) is False
    # A bare key matches on presence regardless of value.
    assert session_matches_tag_filters(tags, ["overflow"]) is True
    assert session_matches_tag_filters(tags, ["missing"]) is False
    # Multiple filters are AND.
    assert session_matches_tag_filters(tags, ["role=backend-lead", "overflow"]) is True
    assert session_matches_tag_filters(tags, ["role=backend-lead", "missing"]) is False
    assert session_matches_tag_filters({}, []) is True


def _make_session(context: Any, tmp_path: Path, sid: str, tags: dict[str, str]) -> None:
    now = datetime.now(UTC)
    context.storage.create_session(
        SessionRecord(
            id=sid,
            backend="codex",
            source=SessionSource.MANAGED,
            title=sid,
            cwd=str(tmp_path),
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(tmp_path / "raw.log"),
            structured_log_path=str(tmp_path / "events.jsonl"),
            tags=tags,
        )
    )


def _build(tmp_path: Path) -> tuple[Any, str]:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    context = app.state.context
    _make_session(context, tmp_path, "lead", {"role": "backend-lead"})
    _make_session(context, tmp_path, "overflow-1", {"overflow": ""})
    _make_session(context, tmp_path, "plain", {})
    token = context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_list_filters_by_tag(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions", params={"tag": "role=backend-lead"}, headers=headers
        )
        assert resp.status_code == 200
        ids = {s["id"] for s in resp.json()["sessions"]}
        assert ids == {"lead"}

        resp = await client.get(
            "/api/sessions", params={"tag": "overflow"}, headers=headers
        )
        assert {s["id"] for s in resp.json()["sessions"]} == {"overflow-1"}


async def test_set_tags_merges_and_unsets(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(app) as client:
        resp = await client.patch(
            "/api/sessions/lead/tags",
            json={"set": {"team": "1"}, "unset": []},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["session"]["tags"] == {"role": "backend-lead", "team": "1"}

        resp = await client.patch(
            "/api/sessions/lead/tags",
            json={"set": {}, "unset": ["role"]},
            headers=headers,
        )
        assert resp.json()["session"]["tags"] == {"team": "1"}


async def test_set_tags_unknown_session_is_404(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.patch(
            "/api/sessions/nope/tags",
            json={"set": {"a": "b"}, "unset": []},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
