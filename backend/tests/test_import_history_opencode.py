"""Importing an OpenCode thread replays its history into the transcript."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from waypoint.backends.opencode.plugin import OpenCodePlugin
from waypoint.schemas import EventKind, EventRecord, SessionRecord, SessionStatus


class _Adapter:
    def __init__(self, history: list[dict[str, Any]] | None) -> None:
        self.history = history
        self.restored: dict[str, Any] = {}

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return {
            "id": session_id,
            "title": "Imported",
            "location": {"directory": "/repo"},
        }

    async def restore_session(
        self, session_id: str, opencode_session_id: str, **kwargs: Any
    ) -> None:
        self.restored = kwargs

    def session_status(self, session_id: str) -> SessionStatus:
        return SessionStatus.IDLE

    async def get_session_messages(self, session_id: str) -> list[dict[str, Any]]:
        if self.history is None:
            raise AssertionError("history is not read when import_history=False")
        return self.history


class _Runtime:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.sessions: list[SessionRecord] = []
        self.calls: list[str] = []
        self.seeded: list[EventRecord] = []
        self.storage = SimpleNamespace(
            list_sessions=lambda: list(self.sessions),
            create_session=self.sessions.append,
            update_session=self._update,
            list_approval_events=lambda _id: [],
            open_question_tool_use_ids=lambda _id: [],
        )

    def _update(self, _session_id: str, **fields: Any) -> SessionRecord:
        for key, value in fields.items():
            setattr(self.sessions[-1], key, value)
        return self.sessions[-1]

    def _generate_session_id(self, backend_id: str) -> str:
        return f"{backend_id}-1"

    def _session_dir(self, _session_id: str) -> Path:
        return self.directory

    def _agent_process_env(
        self, _backend: str, launch_env: dict[str, str]
    ) -> dict[str, str]:
        return dict(launch_env)

    async def _record_system_event(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls.append("note")

    async def seed_thread_history(
        self, _session_id: str, reader: Any, *, enabled: bool
    ) -> int:
        if not enabled:
            return 0
        self.seeded.extend(await reader())
        self.calls.append("seed")
        return len(self.seeded)

    def get_session(self, _session_id: str) -> SessionRecord:
        return self.sessions[-1]


async def _import(
    tmp_path: Path, adapter: _Adapter, *, import_history: bool
) -> tuple[_Runtime, SessionRecord]:
    plugin = OpenCodePlugin()

    async def existing(*_args: Any, **_kwargs: Any) -> _Adapter:
        return adapter

    cast(Any, plugin)._get_or_create_adapter = existing
    cast(Any, plugin)._effective_args = lambda *_args: []
    runtime = _Runtime(tmp_path)
    request = SimpleNamespace(
        thread_id="ses_1",
        launch_target_id=None,
        cwd=None,
        import_history=import_history,
        launch_env={},
    )
    result = await plugin.import_thread(cast(Any, runtime), cast(Any, request))
    return runtime, result


@pytest.mark.asyncio
async def test_import_seeds_history_before_the_imported_note(tmp_path: Path) -> None:
    adapter = _Adapter(
        [{"type": "user", "id": "m1", "text": "hi", "time": {"created": 1000}}]
    )

    runtime, result = await _import(tmp_path, adapter, import_history=True)

    assert runtime.calls == ["seed", "note"]
    [seeded] = runtime.seeded
    assert (seeded.session_id, seeded.kind, seeded.text) == (
        result.id,
        EventKind.USER_INPUT,
        "hi",
    )
    assert (result.cwd, result.title) == ("/repo", "Imported")
    # The recorded mode is applied to the imported session.
    assert result.permission_mode == "ask"
    assert adapter.restored["permissions"] == [
        {"action": "*", "resource": "*", "effect": "ask"}
    ]


@pytest.mark.asyncio
async def test_import_skips_history_when_disabled(tmp_path: Path) -> None:
    runtime, result = await _import(tmp_path, _Adapter(None), import_history=False)

    assert runtime.calls == ["note"]
    assert result.id == "opencode-1"
