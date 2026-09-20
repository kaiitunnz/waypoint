from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from waypoint.api import create_app
from waypoint.attachments import read_text_prefix
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings, load_settings


def _build(tmp_path: Path, **settings_kw: Any) -> tuple[Any, str]:
    settings = Settings(data_dir=tmp_path / "data", **settings_kw)
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


def _seed(app: Any, session_id: str, data: bytes, filename: str = "report.txt") -> str:
    spec = app.state.context.runtime.attachments.save(
        session_id, data=data, filename=filename, content_type="text/plain"
    )
    return spec.id


def _session(app: Any, session_id: str = "s1") -> str:
    now = datetime.now(UTC)
    app.state.context.storage.create_session(
        SessionRecord(
            id=session_id,
            backend="codex",
            source=SessionSource.MANAGED,
            title="t",
            cwd="/tmp",
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="/tmp/raw.log",
            structured_log_path="/tmp/events.jsonl",
        )
    )
    return session_id


# ─── read_text_prefix ───


def test_prefix_reads_small_text_whole(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("hello", encoding="utf-8")
    assert read_text_prefix(path, 64) == ("hello", False, False)


def test_prefix_at_exact_limit_is_not_truncated(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("abcde", encoding="utf-8")
    content, truncated, binary = read_text_prefix(path, 5)
    assert (content, truncated, binary) == ("abcde", False, False)


def test_prefix_truncates_and_returns_leading_text(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("abcdefghij", encoding="utf-8")
    content, truncated, binary = read_text_prefix(path, 4)
    assert (content, truncated, binary) == ("abcd", True, False)


def test_prefix_trims_split_multibyte_character(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("é" * 8, encoding="utf-8")
    # 3 bytes cuts the second 2-byte character in half.
    content, truncated, binary = read_text_prefix(path, 3)
    assert (content, truncated, binary) == ("é", True, False)


def test_prefix_reports_binary_for_nul_bytes(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"pre\x00post")
    content, _, binary = read_text_prefix(path, 64)
    assert content is None
    assert binary is True


def test_prefix_reports_binary_for_undecodable_bytes(tmp_path: Path) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(b"\xff\xfe\xfd")
    content, _, binary = read_text_prefix(path, 64)
    assert content is None
    assert binary is True


def test_prefix_does_not_read_beyond_the_ceiling(tmp_path: Path) -> None:
    path = tmp_path / "big.txt"
    path.write_bytes(b"x" * 10_000)
    content, truncated, _ = read_text_prefix(path, 100)
    assert content is not None
    assert len(content) == 100
    assert truncated is True


# ─── endpoint ───


async def test_preview_requires_a_token(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _session(app)
    attachment_id = _seed(app, "s1", b"hello")
    async with _client(app) as client:
        resp = await client.get(f"/api/sessions/s1/attachments/{attachment_id}/preview")
    assert resp.status_code == 401


async def test_preview_returns_small_text_whole(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _session(app)
    attachment_id = _seed(app, "s1", b"the whole report")
    async with _client(app) as client:
        resp = await client.get(
            f"/api/sessions/s1/attachments/{attachment_id}/preview",
            headers=_auth(token),
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "the whole report"
    assert body["truncated"] is False
    assert body["binary"] is False
    assert body["size"] == len(b"the whole report")


async def test_preview_bounds_an_oversized_report(tmp_path: Path) -> None:
    app, token = _build(tmp_path, attachment_preview_max_bytes=32)
    _session(app)
    attachment_id = _seed(app, "s1", b"y" * 4096)
    async with _client(app) as client:
        resp = await client.get(
            f"/api/sessions/s1/attachments/{attachment_id}/preview",
            headers=_auth(token),
        )
    body = resp.json()
    assert len(body["content"]) == 32
    assert body["truncated"] is True
    # ``size`` is the attachment's full size, not the prefix's.
    assert body["size"] == 4096
    assert body["content_bytes"] == 32


async def test_preview_reports_binary_without_content(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _session(app)
    attachment_id = _seed(app, "s1", b"\x89PNG\x00\r\n", filename="shot.png")
    async with _client(app) as client:
        resp = await client.get(
            f"/api/sessions/s1/attachments/{attachment_id}/preview",
            headers=_auth(token),
        )
    body = resp.json()
    assert body["binary"] is True
    assert body["content"] is None


async def test_preview_404s_for_unknown_attachment(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _session(app)
    async with _client(app) as client:
        resp = await client.get(
            f"/api/sessions/s1/attachments/{'0' * 32}/preview", headers=_auth(token)
        )
    assert resp.status_code == 404


async def test_preview_404s_for_traversal_shaped_id(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _session(app)
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/s1/attachments/..%2f..%2fetc/preview", headers=_auth(token)
        )
    assert resp.status_code == 404


async def test_preview_does_not_cross_sessions(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _session(app, "s1")
    _session(app, "s2")
    attachment_id = _seed(app, "s1", b"secret")
    async with _client(app) as client:
        resp = await client.get(
            f"/api/sessions/s2/attachments/{attachment_id}/preview",
            headers=_auth(token),
        )
    assert resp.status_code == 404


async def test_preview_404s_for_unknown_session(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    _session(app, "s1")
    attachment_id = _seed(app, "s1", b"hello")
    async with _client(app) as client:
        resp = await client.get(
            f"/api/sessions/nope/attachments/{attachment_id}/preview",
            headers=_auth(token),
        )
    assert resp.status_code == 404


# ─── settings ───


def test_preview_limit_may_not_exceed_the_upload_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(attachment_preview_max_bytes=4096, max_upload_bytes=1024)


async def test_preview_reports_prefix_bytes_not_characters(tmp_path: Path) -> None:
    app, token = _build(tmp_path, attachment_preview_max_bytes=32)
    _session(app)
    # Two bytes per character, so a 32-byte prefix is only 16 characters.
    attachment_id = _seed(app, "s1", "é".encode() * 64)
    async with _client(app) as client:
        resp = await client.get(
            f"/api/sessions/s1/attachments/{attachment_id}/preview",
            headers=_auth(token),
        )
    body = resp.json()
    assert len(body["content"]) == 16
    assert body["content_bytes"] == 32


def test_preview_limit_clamps_when_only_the_upload_limit_was_lowered() -> None:
    # An operator who lowers only max_upload_bytes never chose a preview
    # ceiling, so the default must not refuse to start.
    settings = Settings(max_upload_bytes=1024)
    assert settings.attachment_preview_max_bytes == 1024


def test_preview_limit_has_an_absolute_ceiling() -> None:
    with pytest.raises(ValidationError):
        Settings(attachment_preview_max_bytes=8 * 1024 * 1024)


def test_settings_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAYPOINT_ATTACHMENT_PREVIEW_MAX_BYTES", "4096")
    monkeypatch.setenv("WAYPOINT_TASK_OUTPUT_CAPTURE_ENABLED", "false")
    settings = load_settings(None)
    assert settings.attachment_preview_max_bytes == 4096
    assert settings.task_output_capture_enabled is False


def test_inline_budget_clamps_to_a_lowered_preview_ceiling() -> None:
    # Lowering only the preview ceiling must not fail on an eager budget the
    # operator never chose.
    settings = Settings(attachment_preview_max_bytes=1024)
    assert settings.inline_capture_max_bytes == 1024


def test_inline_budget_may_not_exceed_the_preview_ceiling() -> None:
    with pytest.raises(ValidationError):
        Settings(attachment_preview_max_bytes=1024, inline_capture_max_bytes=4096)
