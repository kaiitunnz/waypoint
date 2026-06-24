"""Route-level tests for the workspace preview endpoints.

These exercise the auth, gating, and denylist behavior through the real
FastAPI app (over an in-process ASGI transport) rather than the helper layer.
The routes only read storage/tokens, so the runtime lifespan is not started.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from waypoint.api import create_app
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings


def _build(
    tmp_path: Path,
    settings_kw: dict[str, Any] | None = None,
    session_kw: dict[str, Any] | None = None,
) -> tuple[Any, str]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "notes.md").write_text("hello", encoding="utf-8")
    (workspace / ".env").write_text("SECRET=1", encoding="utf-8")
    git_dir = workspace / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n", encoding="utf-8")

    settings = Settings(data_dir=tmp_path / "data", **(settings_kw or {}))
    app = create_app(settings)
    context = app.state.context
    now = datetime.now(UTC)
    session = SessionRecord(
        id="s1",
        backend="codex",
        source=SessionSource.MANAGED,
        title="t",
        cwd=str(workspace),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(tmp_path / "raw.log"),
        structured_log_path=str(tmp_path / "events.jsonl"),
        **(session_kw or {}),
    )
    context.storage.create_session(session)
    token = context.tokens.issue().token
    return app, token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_tree_lists_dotfiles_but_hides_git(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/tree",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    names = [entry["name"] for entry in resp.json()["entries"]]
    assert "notes.md" in names
    assert ".env" in names  # ordinary dotfiles preview by default
    assert ".git" not in names  # VCS internals stay hidden


async def test_file_reads_text(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "notes.md"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "hello"
    assert body["binary"] is False


async def test_dotdir_is_denied(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": ".git/config"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


async def test_traversal_and_missing(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    async with _client(app) as client:
        escape = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "../../../../etc/passwd"},
            headers=headers,
        )
        missing = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "nope.txt"},
            headers=headers,
        )
    assert escape.status_code == 403
    assert missing.status_code == 404


async def test_requires_token(tmp_path: Path) -> None:
    app, _ = _build(tmp_path)
    async with _client(app) as client:
        resp = await client.get("/api/sessions/s1/workspace/tree")
    assert resp.status_code == 401


async def test_remote_session_rejected(tmp_path: Path) -> None:
    app, token = _build(tmp_path, session_kw={"launch_target_id": "devbox"})
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/tree",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400


async def test_disabled_returns_404(tmp_path: Path) -> None:
    app, token = _build(tmp_path, settings_kw={"workspace_preview_enabled": False})
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/tree",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


async def test_raw_validates_query_token(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    async with _client(app) as client:
        bad = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "notes.md", "raw": "1", "token": "bogus"},
        )
        good = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": "notes.md", "raw": "1", "token": token},
        )
    assert bad.status_code == 401
    assert good.status_code == 200
    assert good.text == "hello"


async def test_empty_denylist_disables_filtering(tmp_path: Path) -> None:
    app, token = _build(tmp_path, settings_kw={"workspace_denylist": []})
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/workspace/file",
            params={"path": ".git/config"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["content"] == "[core]\n"
