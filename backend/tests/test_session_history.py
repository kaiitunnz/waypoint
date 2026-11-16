"""Live-only session list frames, paged session history, and history search."""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from waypoint import session_search
from waypoint.api import create_app
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.session_search import SearchBudgetExceeded, matches, parse_query
from waypoint.settings import Settings
from waypoint.storage import Storage

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _record(
    session_id: str,
    *,
    status: SessionStatus = SessionStatus.EXITED,
    minutes: int = 0,
    title: str | None = None,
    pinned: bool = False,
    source: SessionSource = SessionSource.MANAGED,
    cwd: str = "/work/proj",
    repo_name: str | None = None,
) -> SessionRecord:
    at = BASE + timedelta(minutes=minutes)
    return SessionRecord(
        id=session_id,
        backend="codex",
        source=source,
        title=title or session_id,
        cwd=cwd,
        repo_name=repo_name,
        status=status,
        created_at=at,
        updated_at=at,
        last_event_at=at,
        pinned_at=at if pinned else None,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
    )


def _seed(storage: Storage, *records: SessionRecord) -> None:
    for record in records:
        storage.create_session(record)


def _build(tmp_path: Path) -> tuple[Any, Any, str]:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    context = app.state.context
    return app, context, context.tokens.issue().token


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ─── Storage ───


def test_live_sessions_exclude_unpinned_history(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    _seed(
        storage,
        _record("old-exited", minutes=1),
        _record("pinned-exited", minutes=2, pinned=True),
        _record("idle", status=SessionStatus.IDLE, minutes=3),
        _record("errored", status=SessionStatus.ERROR, minutes=4),
    )
    live = [session.id for session in storage.list_live_sessions()]
    assert live == ["errored", "idle", "pinned-exited"]
    assert storage.count_history_sessions() == 1


def test_live_query_and_history_count_avoid_table_scans(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    _seed(storage, *(_record(f"s{i}", minutes=i) for i in range(20)))
    statements: list[str] = []
    storage.connection.set_trace_callback(statements.append)
    storage.list_live_sessions()
    storage.count_history_sessions()
    storage.count_history_sessions(("assistant", "telemetry"))
    storage.list_history_sessions(limit=5, exclude_sources=("assistant",))
    storage.connection.set_trace_callback(None)
    assert len(statements) == 4
    for statement in statements:
        plan = storage.connection.execute(f"EXPLAIN QUERY PLAN {statement}").fetchall()
        details = [row["detail"] for row in plan]
        # Walking an index is fine; reading the table row by row is not.
        assert not any(
            detail.startswith("SCAN sessions") and "INDEX" not in detail
            for detail in details
        ), details


def test_history_keyset_pages_cover_every_row_once(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    _seed(storage, *(_record(f"s{i}", minutes=i) for i in range(7)))
    # A tie on last_event_at must still page deterministically by id.
    _seed(storage, _record("s3b", minutes=3))
    seen: list[str] = []
    cursor = None
    while True:
        page, has_more, cursor = storage.list_history_sessions(limit=3, cursor=cursor)
        seen.extend(session.id for session in page)
        if not has_more:
            assert cursor is None
            break
    assert seen == ["s6", "s5", "s4", "s3b", "s3", "s2", "s1", "s0"]


def test_history_respects_excluded_sources(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    _seed(
        storage,
        _record("managed", minutes=1),
        _record("assistant", minutes=2, source=SessionSource.ASSISTANT),
    )
    assert storage.count_history_sessions(("assistant",)) == 1
    page, _, _ = storage.list_history_sessions(limit=10, exclude_sources=("assistant",))
    assert [session.id for session in page] == ["managed"]


def test_get_sessions_by_ids_keeps_requested_order(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    _seed(storage, _record("a"), _record("b"), _record("c"))
    found = storage.get_sessions_by_ids(["c", "missing", "a", "c"])
    assert [session.id for session in found] == ["c", "a"]


# ─── Search parity with frontend/src/lib/search.ts ───


ROW = {
    "id": "abc123",
    "title": "Fix Login Bug",
    "cwd": "/home/me/waypoint",
    "repo_name": "waypoint",
    "branch": "fix/login",
    "backend": "claude_code",
    "status": "idle",
    "focus": 1,
    "created_at": "2026-01-01T00:00:00.123456+00:00",
}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", True),
        ("login", True),
        ("LOGIN bug", True),
        ("login nomatch", False),
        ("nomatch OR login", True),
        ("nomatch AND login", False),
        ("repo:way", True),
        ("dir:/home/me", True),
        ("agent:claude", True),
        ("title:waypoint", False),
        ('"login bug"', True),
        ("'fix login'", True),
        ("/^fix\\s+lo/", True),
        ("/^login/", False),
        ("/(/", False),
        ("status:active", True),
        ("state:idle", True),
        ("status:exited", False),
        ("focus:true", True),
        ("created_at:00.123456Z", True),
        ("id:abc", True),
        ("unknown:abc", False),
        ("title:", False),
    ],
)
def test_search_matches_frontend_semantics(query: str, expected: bool) -> None:
    deadline = time.monotonic() + 5
    assert matches(ROW, parse_query(query), deadline) is expected


def test_search_virtual_status_for_exited_rows() -> None:
    row = {**ROW, "status": "exited"}
    deadline = time.monotonic() + 5
    assert matches(row, parse_query("status:exited"), deadline)
    assert not matches(row, parse_query("status:active"), deadline)


def test_search_regex_past_deadline_raises() -> None:
    with pytest.raises(SearchBudgetExceeded):
        matches(ROW, parse_query("/zzz/"), time.monotonic() - 1)


# ─── API ───


async def test_history_endpoint_pages_and_counts(tmp_path: Path) -> None:
    app, context, token = _build(tmp_path)
    _seed(
        context.runtime.storage,
        *(_record(f"s{i}", minutes=i) for i in range(5)),
        _record("live", status=SessionStatus.IDLE, minutes=9),
    )
    async with _client(app) as client:
        first = (
            await client.get("/api/sessions/history?limit=2", headers=_auth(token))
        ).json()
        assert [s["id"] for s in first["sessions"]] == ["s4", "s3"]
        assert first["has_more"] is True
        assert first["total"] == 5
        rest = (
            await client.get(
                "/api/sessions/history",
                params={"limit": 10, "cursor": first["cursor"]},
                headers=_auth(token),
            )
        ).json()
        assert [s["id"] for s in rest["sessions"]] == ["s2", "s1", "s0"]
        assert rest["has_more"] is False
        assert rest["cursor"] is None
        counts = (
            await client.get("/api/sessions/history?limit=0", headers=_auth(token))
        ).json()
        assert counts == {"sessions": [], "has_more": False, "cursor": None, "total": 5}
        bad = await client.get(
            "/api/sessions/history?cursor=garbage", headers=_auth(token)
        )
        assert bad.status_code == 400


async def test_history_search_pages_matches(tmp_path: Path) -> None:
    app, context, token = _build(tmp_path)
    _seed(
        context.runtime.storage,
        *(_record(f"alpha{i}", minutes=i, title=f"alpha {i}") for i in range(4)),
        _record("beta", minutes=10, title="beta"),
        _record(
            "alpha-telemetry", minutes=11, title="alpha", source=SessionSource.TELEMETRY
        ),
    )
    async with _client(app) as client:
        params: dict[str, Any] = {
            "q": "alpha",
            "limit": 2,
            "exclude_source": "telemetry",
        }
        first = (
            await client.get(
                "/api/sessions/history", params=params, headers=_auth(token)
            )
        ).json()
        assert [s["id"] for s in first["sessions"]] == ["alpha3", "alpha2"]
        assert first["total"] == 4
        second = (
            await client.get(
                "/api/sessions/history",
                params={**params, "cursor": first["cursor"]},
                headers=_auth(token),
            )
        ).json()
        assert [s["id"] for s in second["sessions"]] == ["alpha1", "alpha0"]
        assert second["has_more"] is False


async def test_history_search_over_budget_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, context, token = _build(tmp_path)
    _seed(context.runtime.storage, _record("s0"))
    monkeypatch.setattr(session_search, "SEARCH_BUDGET_SECONDS", -1.0)
    async with _client(app) as client:
        resp = await client.get(
            "/api/sessions/history", params={"q": "/s0/"}, headers=_auth(token)
        )
    assert resp.status_code == 400


async def test_sessions_list_scopes_and_id_filter(tmp_path: Path) -> None:
    app, context, token = _build(tmp_path)
    _seed(
        context.runtime.storage,
        _record("old", minutes=1),
        _record("live", status=SessionStatus.RUNNING, minutes=2),
    )
    async with _client(app) as client:
        everything = (await client.get("/api/sessions", headers=_auth(token))).json()
        assert {s["id"] for s in everything["sessions"]} == {"old", "live"}
        live = (
            await client.get("/api/sessions?scope=live", headers=_auth(token))
        ).json()
        assert [s["id"] for s in live["sessions"]] == ["live"]
        assert live["scope"] == "live"
        assert live["history_count"] == 1
        assert live["history_revision"].endswith(".1")
        by_id = (
            await client.get("/api/sessions?id=old&id=nope", headers=_auth(token))
        ).json()
        assert [s["id"] for s in by_id["sessions"]] == ["old"]
        for combined in (
            "scope=live&spawned_by=x",
            "id=old&tag=a:b",
            "scope=live&id=old",
        ):
            resp = await client.get(f"/api/sessions?{combined}", headers=_auth(token))
            assert resp.status_code == 400, combined


async def test_history_revision_changes_on_mutation_not_streaming(
    tmp_path: Path,
) -> None:
    _, context, _ = _build(tmp_path)
    runtime = context.runtime
    _seed(runtime.storage, _record("old", minutes=1))
    before = runtime.session_list_envelope().payload["history_revision"]
    assert runtime.session_list_envelope().payload["history_revision"] == before
    await runtime.set_title("old", "renamed")
    assert runtime.session_list_envelope().payload["history_revision"] != before


def test_ws_initial_frame_is_live_only(tmp_path: Path) -> None:
    app, context, token = _build(tmp_path)
    _seed(
        context.runtime.storage,
        _record("old", minutes=1),
        _record("live", status=SessionStatus.IDLE, minutes=2),
    )
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/sessions?token={token}") as ws:
            frame = ws.receive_json()
    assert frame["type"] == "session_list_update"
    assert [s["id"] for s in frame["payload"]["sessions"]] == ["live"]
    assert frame["payload"]["history_count"] == 1


async def test_delete_exited_sweep_spares_revived_and_assistant(
    tmp_path: Path,
) -> None:
    _, context, _ = _build(tmp_path)
    runtime = context.runtime
    _seed(
        runtime.storage,
        _record("gone-1", minutes=1),
        _record("gone-2", minutes=2, pinned=True),
        _record("revived", minutes=3),
        _record("helper", minutes=4, source=SessionSource.ASSISTANT),
    )
    assert runtime.delete_exited_sessions() == 3
    # A second request while the sweep runs does not start another.
    assert runtime.delete_exited_sessions() == 0
    runtime.storage.update_session("revived", status=SessionStatus.IDLE)
    task = runtime._delete_exited_task
    assert task is not None
    assert await task == 2
    remaining = {session.id for session in runtime.list_sessions()}
    assert remaining == {"revived", "helper"}


def test_storage_rejects_unknown_columns_in_search_projection(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    _seed(storage, _record("s0"))
    rows = storage.history_search_rows(["title", "id; DROP TABLE sessions"])
    assert rows == [
        {"title": "s0", "id": "s0", "last_event_at": rows[0]["last_event_at"]}
    ]
