from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

from waypoint.backends.tmux.adapter import TmuxTarget
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    SessionAttachRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    runtime = SessionRuntime(settings, Storage(settings.database_path))
    target = TmuxTarget(
        session="work",
        window="1",
        pane="%3",
        cwd=str(tmp_path),
        pane_dead=False,
        pane_pid=None,
    )

    async def describe_target(_: str) -> TmuxTarget:
        return target

    async def capture_snapshot(_: str, __: int) -> str:
        return ""

    async def pipe_output(_: str, __: Path) -> None:
        return None

    monkeypatch.setattr(runtime.tmux, "describe_target", describe_target)
    monkeypatch.setattr(runtime.tmux, "capture_snapshot", capture_snapshot)
    monkeypatch.setattr(runtime.tmux, "pipe_output", pipe_output)
    monkeypatch.setattr(runtime, "_ensure_monitor", lambda _: None)
    return runtime


def _lead(runtime: SessionRuntime, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    runtime.storage.create_session(
        SessionRecord(
            id="lead",
            backend="codex",
            source=SessionSource.MANAGED,
            transport="codex_app_server",
            title="Lead",
            cwd=str(tmp_path),
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(tmp_path / "raw.log"),
            structured_log_path=str(tmp_path / "events.jsonl"),
        )
    )


@pytest.mark.asyncio
async def test_attach_tmux_stamps_spawner(
    runtime: SessionRuntime, tmp_path: Path
) -> None:
    _lead(runtime, tmp_path)
    session = await runtime.attach_tmux(
        SessionAttachRequest(
            tmux_target="work:1.0", backend_hint="codex", spawner_session_id="lead"
        )
    )
    assert session.source == SessionSource.ATTACHED_TMUX
    assert session.spawner_session_id == "lead"
    assert runtime.get_session(session.id).spawner_session_id == "lead"


@pytest.mark.asyncio
async def test_attach_tmux_without_spawner_has_no_owner(
    runtime: SessionRuntime,
) -> None:
    session = await runtime.attach_tmux(SessionAttachRequest(tmux_target="%3"))
    assert session.spawner_session_id is None


@pytest.mark.asyncio
async def test_attach_tmux_rejects_unknown_spawner(runtime: SessionRuntime) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await runtime.attach_tmux(
            SessionAttachRequest(tmux_target="%3", spawner_session_id="ghost")
        )
    assert exc_info.value.status_code == 400
    assert "unknown spawner session: ghost" in str(exc_info.value.detail)
    assert runtime.storage.list_sessions() == []
