from datetime import UTC, datetime
from typing import Any, cast

import pytest

from waypoint.config import Settings
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    Backend,
    EventKind,
    SessionCreateRequest,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
    SessionTransport,
)
from waypoint.server_config import SshLaunchTargetConfig
from waypoint.storage import Storage


class FakeStructuredAdapter:
    def __init__(self, pending: bool = False) -> None:
        self.pending = pending
        self.inputs: list[tuple[str, str]] = []

    async def send_input(self, session_id: str, text: str) -> None:
        self.inputs.append((session_id, text))

    def has_pending_approval(self, session_id: str) -> bool:
        return self.pending


class FakeClaudeAdapter(FakeStructuredAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.start_calls: list[tuple[str, str, str, Any]] = []

    async def start_session(
        self,
        session_id: str,
        cwd: str,
        claude_session_id: str,
        launch_factory_override: Any = None,
    ) -> str:
        self.start_calls.append(
            (session_id, cwd, claude_session_id, launch_factory_override)
        )
        return claude_session_id


def make_runtime(tmp_path) -> tuple[SessionRuntime, Storage, Settings]:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    return runtime, storage, settings


def make_session(settings: Settings, **overrides) -> SessionRecord:
    session_dir = settings.sessions_dir / overrides.get("id", "sess")
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    return SessionRecord(
        id=overrides.get("id", "sess"),
        backend=overrides.get("backend", Backend.CODEX),
        source=SessionSource.MANAGED,
        transport=overrides.get("transport", SessionTransport.CODEX_APP_SERVER),
        title="Session",
        cwd="/tmp/project",
        remote_cwd=overrides.get("remote_cwd"),
        status=overrides.get("status", SessionStatus.IDLE),
        created_at=now,
        updated_at=now,
        last_event_at=now,
        thread_id=overrides.get("thread_id", "thread-1"),
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
    )


@pytest.mark.asyncio
async def test_handle_input_builtin_status_intercepts_structured_session(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter()
    runtime.codex = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)

    updated = await runtime.handle_input("sess", SessionInputRequest(text="/status"))

    assert fake.inputs == []
    assert updated.status == SessionStatus.IDLE
    events = storage.list_events("sess")
    assert [event.kind for event in events] == [
        EventKind.USER_INPUT,
        EventKind.SYSTEM_NOTE,
    ]
    assert events[0].text == "/status"
    assert "Transport: codex_app_server" in events[1].text
    assert "Thread: thread-1" in events[1].text
    assert events[1].metadata["builtin_command"] == "/status"


@pytest.mark.asyncio
async def test_handle_input_builtin_permissions_reports_pending_approval(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter(pending=True)
    runtime.claude = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend=Backend.CLAUDE_CODE,
        transport=SessionTransport.CLAUDE_CLI,
        thread_id="claude-thread",
    )
    storage.create_session(session)

    updated = await runtime.handle_input(
        "claude-sess", SessionInputRequest(text="/permissions")
    )

    assert fake.inputs == []
    assert updated.status == SessionStatus.IDLE
    events = storage.list_events("claude-sess")
    assert events[-1].kind == EventKind.SYSTEM_NOTE
    assert "Pending approval: yes" in events[-1].text
    assert "Approve for session" in events[-1].text
    assert events[-1].metadata["builtin_command"] == "/permissions"


@pytest.mark.asyncio
async def test_handle_input_builtin_compact_forwards_to_claude_cli(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter()
    runtime.claude = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend=Backend.CLAUDE_CODE,
        transport=SessionTransport.CLAUDE_CLI,
    )
    storage.create_session(session)

    await runtime.handle_input("claude-sess", SessionInputRequest(text="/compact"))

    # Claude's CLI handles /compact itself in stream-json mode, so the runtime
    # forwards the text to the adapter as-is rather than intercepting.
    assert fake.inputs == [("claude-sess", "/compact")]


@pytest.mark.asyncio
async def test_handle_input_builtin_compact_invokes_codex_thread_compact(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)

    class CodexFake(FakeStructuredAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.compact_calls: list[str] = []

        async def compact_thread(self, session_id: str) -> None:
            self.compact_calls.append(session_id)

    fake = CodexFake()
    runtime.codex = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)

    updated = await runtime.handle_input("sess", SessionInputRequest(text="/compact"))

    assert fake.compact_calls == ["sess"]
    assert fake.inputs == []
    assert updated.status == SessionStatus.RUNNING
    events = storage.list_events("sess")
    assert events[-1].metadata["builtin_command"] == "/compact"
    assert "Compacting codex thread" in events[-1].text


@pytest.mark.asyncio
async def test_handle_input_unknown_slash_command_forwards_to_structured_session(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter()
    runtime.codex = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)

    updated = await runtime.handle_input(
        "sess", SessionInputRequest(text="/model", submit=True)
    )

    assert fake.inputs == [("sess", "/model")]
    assert updated.status == SessionStatus.RUNNING
    events = storage.list_events("sess")
    assert len(events) == 1
    assert events[0].kind == EventKind.USER_INPUT
    assert events[0].text == "/model"


@pytest.mark.asyncio
async def test_create_session_uses_structured_claude_for_ssh_target(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        ssh_targets=[
            SshLaunchTargetConfig(
                id="devbox",
                name="Devbox",
                ssh_destination="dev@example.com",
                supported_backends=[Backend.CLAUDE_CODE],
                default_remote_cwd="~/workspace",
            )
        ],
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    fake = FakeClaudeAdapter()
    runtime.claude = cast(Any, fake)
    runtime.claude_hook = cast(
        Any,
        type(
            "HookBundle",
            (),
            {
                "hook_script_path": tmp_path / "hook.py",
                "secret": "hook-secret",
            },
        )(),
    )
    runtime.claude_hook.hook_script_path.write_text(
        "#!/usr/bin/env python3\nprint('hook')\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        "waypoint.runtime.build_remote_claude_launch_factory",
        lambda *args, **kwargs: "remote-launch-factory",
    )

    session = await runtime.create_session(
        SessionCreateRequest(
            backend=Backend.CLAUDE_CODE,
            cwd="/tmp/project",
            remote_cwd="~/workspace",
            launch_target_id="devbox",
            title=None,
            args=[],
            source_mode=SessionSource.MANAGED,
        )
    )

    assert session.transport == SessionTransport.CLAUDE_CLI
    assert session.launch_target_id == "devbox"
    assert session.remote_cwd == "~/workspace"
    assert fake.start_calls == [
        (
            session.id,
            "~/workspace",
            session.thread_id,
            "remote-launch-factory",
        )
    ]
