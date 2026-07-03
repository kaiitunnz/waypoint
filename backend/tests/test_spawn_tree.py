"""Recursive spawn-tree and idle helpers: the API descendant walk and route,
plus the CLI-side duration parser, idle filter, and tree builder."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import typer

from waypoint.api import _descendant_ids, create_app
from waypoint.cli import _build_session_tree, _filter_idle, _parse_duration
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings


def _record(sid: str, parent: str | None) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id=sid,
        backend="codex",
        source=SessionSource.MANAGED,
        title=sid,
        cwd="/tmp",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
        spawner_session_id=parent,
    )


def test_descendant_ids_collects_transitively() -> None:
    sessions = [
        _record("root", None),
        _record("a", "root"),
        _record("b", "a"),
        _record("c", "root"),
        _record("unrelated", None),
    ]
    assert _descendant_ids(sessions, "root") == {"a", "b", "c"}
    assert _descendant_ids(sessions, "a") == {"b"}
    assert _descendant_ids(sessions, "leaf-missing") == set()


def test_descendant_ids_survives_a_cycle() -> None:
    # x -> y -> x is a pathological loop; the walk must terminate.
    sessions = [_record("x", "y"), _record("y", "x")]
    assert _descendant_ids(sessions, "x") == {"y"}


def test_parse_duration_units() -> None:
    assert _parse_duration("90s") == 90
    assert _parse_duration("5m") == 300
    assert _parse_duration("2h") == 7200
    assert _parse_duration("1d") == 86400
    assert _parse_duration("45") == 45  # bare number = seconds
    for bad in ("", "abc", "-5m", "5x"):
        with pytest.raises(typer.BadParameter):
            _parse_duration(bad)


def test_filter_idle_keeps_only_stale() -> None:
    now = datetime.now(UTC)
    fresh = {"id": "fresh", "last_event_at": now.isoformat()}
    stale = {"id": "stale", "last_event_at": (now - timedelta(hours=2)).isoformat()}
    no_ts = {"id": "no_ts"}
    kept = _filter_idle([fresh, stale, no_ts], idle_seconds=3600)
    assert [s["id"] for s in kept] == ["stale"]


def test_build_session_tree_nests_children() -> None:
    now = datetime.now(UTC).isoformat()
    sessions = [
        {"id": "root", "title": "R", "status": "idle", "last_event_at": now},
        {"id": "a", "spawner_session_id": "root", "last_event_at": now},
        {"id": "b", "spawner_session_id": "a", "last_event_at": now},
        {"id": "c", "spawner_session_id": "root", "last_event_at": now},
    ]
    tree = _build_session_tree(sessions, "root")
    assert tree is not None
    assert tree["id"] == "root"
    kids = {child["id"]: child for child in tree["children"]}
    assert set(kids) == {"a", "c"}
    assert [g["id"] for g in kids["a"]["children"]] == ["b"]
    assert _build_session_tree(sessions, "nope") is None


def test_build_session_tree_survives_a_cycle() -> None:
    sessions = [
        {"id": "x", "spawner_session_id": "y"},
        {"id": "y", "spawner_session_id": "x"},
    ]
    tree = _build_session_tree(sessions, "x")
    assert tree is not None
    # y is reached once; the loop back to x is cut by the visited set.
    assert [child["id"] for child in tree["children"]] == ["y"]
    assert tree["children"][0]["children"] == []


def _build(tmp_path: Path) -> tuple[Any, str]:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    context = app.state.context
    for sid, parent in [("root", None), ("a", "root"), ("b", "a"), ("c", "root")]:
        context.storage.create_session(_record(sid, parent))
    token = context.tokens.issue().token
    return app, token


async def test_list_recursive_includes_whole_subtree(tmp_path: Path) -> None:
    app, token = _build(tmp_path)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        flat = await client.get(
            "/api/sessions", params={"spawned_by": "root"}, headers=headers
        )
        assert {s["id"] for s in flat.json()["sessions"]} == {"a", "c"}

        deep = await client.get(
            "/api/sessions",
            params={"spawned_by": "root", "recursive": "true"},
            headers=headers,
        )
        assert {s["id"] for s in deep.json()["sessions"]} == {"a", "b", "c"}
