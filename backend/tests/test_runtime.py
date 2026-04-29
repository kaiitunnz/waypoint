from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from waypoint.config import Settings
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    Backend,
    CodexThreadImportRequest,
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


class FakeCodexRuntimeAdapter(FakeStructuredAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.restore_calls: list[tuple[str, str, str, str | None, Any]] = []

    async def restore_session(
        self,
        session_id: str,
        cwd: str,
        thread_id: str,
        remote_cwd: str | None = None,
        client_factory_override: Any = None,
    ) -> None:
        self.restore_calls.append(
            (session_id, cwd, thread_id, remote_cwd, client_factory_override)
        )


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


def make_thread(**overrides: Any) -> Any:
    git_info = overrides.pop("git_info", None)
    return SimpleNamespace(
        id=overrides.pop("id", "thread-1"),
        name=overrides.pop("name", None),
        preview=overrides.pop("preview", "Fix flaky test"),
        cwd=overrides.pop("cwd", "/tmp/project"),
        created_at=overrides.pop("created_at", 1_700_000_000),
        updated_at=overrides.pop("updated_at", 1_700_000_300),
        ephemeral=overrides.pop("ephemeral", False),
        git_info=git_info,
    )


@pytest.mark.asyncio
async def test_handle_input_status_forwards_to_codex(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter()
    runtime.codex = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)

    await runtime.handle_input("sess", SessionInputRequest(text="/status"))

    # Codex's app-server has no Waypoint-side renderer — `/status` flows to
    # the agent so the underlying backend can decide how to respond.
    assert fake.inputs == [("sess", "/status")]
    events = storage.list_events("sess")
    assert [event.kind for event in events] == [EventKind.USER_INPUT]
    assert events[0].text == "/status"


@pytest.mark.asyncio
async def test_handle_input_permissions_forwards_to_claude_cli(tmp_path) -> None:
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

    await runtime.handle_input("claude-sess", SessionInputRequest(text="/permissions"))

    # Claude CLI's stream-json mode parses slash commands itself; forwarding
    # `/permissions` lets the CLI emit its own system/status response instead
    # of Waypoint synthesising one.
    assert fake.inputs == [("claude-sess", "/permissions")]


@pytest.mark.asyncio
async def test_handle_input_help_forwards_to_backend(tmp_path) -> None:
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

    await runtime.handle_input("claude-sess", SessionInputRequest(text="/help"))

    assert fake.inputs == [("claude-sess", "/help")]


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


@pytest.mark.asyncio
async def test_list_importable_codex_threads_filters_existing_session(
    monkeypatch, tmp_path
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    storage.create_session(make_session(settings, thread_id="thread-1"))
    thread_one = make_thread(id="thread-1", name="Existing")
    thread_two = make_thread(
        id="thread-2",
        name=None,
        preview="Investigate remote import support",
        cwd=SimpleNamespace(root="/tmp/other-project"),
        git_info=SimpleNamespace(
            branch="feature/import", origin_url="git@github.com:acme/other-project.git"
        ),
    )

    async def fake_run(launch_target_id, operation, **kwargs):
        return [thread_one, thread_two]

    monkeypatch.setattr(runtime, "_run_codex_client_operation", fake_run)

    threads = await runtime.list_importable_codex_threads()

    assert [thread.id for thread in threads] == ["thread-2"]
    assert threads[0].title == "Investigate remote import support"
    assert threads[0].repo_name == "other-project"
    assert threads[0].branch == "feature/import"


@pytest.mark.asyncio
async def test_import_codex_thread_for_remote_target_uses_thread_cwd(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        ssh_targets=[
            SshLaunchTargetConfig(
                id="devbox",
                name="Devbox",
                ssh_destination="dev@example.com",
                supported_backends=[Backend.CODEX],
                default_remote_cwd="~/workspace",
            )
        ],
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    fake = FakeCodexRuntimeAdapter()
    runtime.codex = cast(Any, fake)
    thread = make_thread(
        id="thread-9",
        name="Existing remote thread",
        cwd=SimpleNamespace(root="/srv/worktree/project"),
        git_info=SimpleNamespace(
            branch="main", origin_url="ssh://git.example.com/team/project.git"
        ),
    )

    async def fake_read(thread_id: str, launch_target_id: str | None) -> Any:
        assert thread_id == "thread-9"
        assert launch_target_id == "devbox"
        return thread

    monkeypatch.setattr(runtime, "_read_codex_thread", fake_read)
    monkeypatch.setattr(
        runtime, "_codex_client_factory", lambda launch_target_id: "remote-factory"
    )

    session = await runtime.import_codex_thread(
        CodexThreadImportRequest(thread_id="thread-9", launch_target_id="devbox")
    )

    assert session.transport == SessionTransport.CODEX_APP_SERVER
    assert session.cwd == "/srv/worktree/project"
    assert session.remote_cwd == "/srv/worktree/project"
    assert session.launch_target_id == "devbox"
    assert session.repo_name == "project"
    assert session.branch == "main"
    assert fake.restore_calls == [
        (
            session.id,
            "/srv/worktree/project",
            "thread-9",
            "/srv/worktree/project",
            "remote-factory",
        )
    ]
    events = storage.list_events(session.id)
    assert events[-1].kind == EventKind.SYSTEM_NOTE
    assert "Imported stored Codex thread via SSH target Devbox" in events[-1].text
