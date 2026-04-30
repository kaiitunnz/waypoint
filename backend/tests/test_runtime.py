from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from waypoint.claude_threads import ClaudeThreadInfo
from waypoint.config import Settings
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    Backend,
    ClaudeThreadImportRequest,
    CodexThreadImportRequest,
    EventKind,
    SessionApprovalRequest,
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
        self.turn_params_calls: list[tuple[str, dict[str, Any] | None]] = []
        self.approval_calls: list[tuple[str, str]] = []

    async def send_input(
        self, session_id: str, text: str, turn_params: dict[str, Any] | None = None
    ) -> None:
        self.inputs.append((session_id, text))
        self.turn_params_calls.append((session_id, turn_params))

    def has_pending_approval(self, session_id: str) -> bool:
        return self.pending

    async def respond_to_approval(self, session_id: str, decision: str) -> bool:
        self.approval_calls.append((session_id, decision))
        return True


class FakeClaudeAdapter(FakeStructuredAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.start_calls: list[
            tuple[str, str, str, Any, str | None, str | None, str | None]
        ] = []
        self.restore_calls: list[
            tuple[str, str, str, Any, str | None, str | None, str | None]
        ] = []
        self.terminate_calls: list[str] = []
        self.permission_mode_calls: list[tuple[str, str]] = []
        self.model_calls: list[tuple[str, str | None]] = []
        self.effort_calls: list[tuple[str, str | None]] = []
        self.modes: dict[str, str] = {}
        self.models: dict[str, str | None] = {}
        self.efforts: dict[str, str | None] = {}

    async def terminate_session(self, session_id: str) -> bool:
        self.terminate_calls.append(session_id)
        return True

    async def restore_session(
        self,
        session_id: str,
        cwd: str,
        claude_session_id: str,
        launch_factory_override: Any = None,
        permission_mode: str | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        self.restore_calls.append(
            (
                session_id,
                cwd,
                claude_session_id,
                launch_factory_override,
                permission_mode,
                model,
                effort,
            )
        )
        if model is not None:
            self.models[session_id] = model
        if effort is not None:
            self.efforts[session_id] = effort

    async def start_session(
        self,
        session_id: str,
        cwd: str,
        claude_session_id: str,
        launch_factory_override: Any = None,
        permission_mode: str | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> str:
        self.start_calls.append(
            (
                session_id,
                cwd,
                claude_session_id,
                launch_factory_override,
                permission_mode,
                model,
                effort,
            )
        )
        if model is not None:
            self.models[session_id] = model
        if effort is not None:
            self.efforts[session_id] = effort
        return claude_session_id

    async def set_permission_mode(self, session_id: str, mode: str) -> None:
        self.permission_mode_calls.append((session_id, mode))
        self.modes[session_id] = mode

    async def set_model(self, session_id: str, model: str | None) -> None:
        self.model_calls.append((session_id, model))
        self.models[session_id] = model

    async def set_effort(self, session_id: str, effort: str | None) -> None:
        self.effort_calls.append((session_id, effort))
        self.efforts[session_id] = effort

    def session_permission_mode(self, session_id: str) -> str | None:
        return self.modes.get(session_id)

    def session_model(self, session_id: str) -> str | None:
        return self.models.get(session_id)

    def session_effort(self, session_id: str) -> str | None:
        return self.efforts.get(session_id)


class FakeCodexRuntimeAdapter(FakeStructuredAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.restore_calls: list[tuple[str, str, str, Any, str | None, str | None]] = []
        self.terminate_calls: list[str] = []
        self.model_calls: list[tuple[str, str | None]] = []
        self.effort_calls: list[tuple[str, str | None]] = []
        self.models: dict[str, str | None] = {}
        self.efforts: dict[str, str | None] = {}

    async def terminate_session(self, session_id: str) -> bool:
        self.terminate_calls.append(session_id)
        return True

    async def restore_session(
        self,
        session_id: str,
        cwd: str,
        thread_id: str,
        client_factory_override: Any = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        self.restore_calls.append(
            (session_id, cwd, thread_id, client_factory_override, model, effort)
        )
        if model is not None:
            self.models[session_id] = model
        if effort is not None:
            self.efforts[session_id] = effort

    async def set_model(self, session_id: str, model: str | None) -> None:
        self.model_calls.append((session_id, model))
        self.models[session_id] = model

    async def set_effort(self, session_id: str, effort: str | None) -> None:
        self.effort_calls.append((session_id, effort))
        self.efforts[session_id] = effort

    def session_model(self, session_id: str) -> str | None:
        return self.models.get(session_id)

    def session_effort(self, session_id: str) -> str | None:
        return self.efforts.get(session_id)


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
        launch_target_id=overrides.get("launch_target_id"),
        status=overrides.get("status", SessionStatus.IDLE),
        created_at=now,
        updated_at=now,
        last_event_at=now,
        thread_id=overrides.get("thread_id", "thread-1"),
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
        permission_mode=overrides.get("permission_mode"),
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
async def test_handle_input_reattaches_exited_codex_session(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    runtime.codex = cast(Any, fake)
    session = make_session(
        settings,
        status=SessionStatus.EXITED,
        thread_id="thread-resume",
    )
    storage.create_session(session)

    updated = await runtime.handle_input(
        "sess", SessionInputRequest(text="picking back up")
    )

    # Reattach uses the stored thread_id to resume the existing Codex thread,
    # then the input forwards through the freshly attached client.
    assert fake.restore_calls == [
        ("sess", "/tmp/project", "thread-resume", None, None, None)
    ]
    # Stale adapter state is torn down before the respawn so we don't orphan
    # a client/process keyed under the same session id.
    assert fake.terminate_calls == ["sess"]
    assert fake.inputs == [("sess", "picking back up")]
    assert updated.status == SessionStatus.RUNNING


@pytest.mark.asyncio
async def test_handle_input_reattaches_errored_claude_session(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    runtime.claude = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend=Backend.CLAUDE_CODE,
        transport=SessionTransport.CLAUDE_CLI,
        thread_id="claude-thread",
        status=SessionStatus.ERROR,
        permission_mode="default",
    )
    storage.create_session(session)

    updated = await runtime.handle_input(
        "claude-sess", SessionInputRequest(text="retry please")
    )

    # ERROR is treated like EXITED for reattach: respawn the CLI with --resume
    # plus the stored permission/model/effort so the conversation continues.
    assert fake.restore_calls == [
        (
            "claude-sess",
            "/tmp/project",
            "claude-thread",
            None,
            "default",
            None,
            None,
        )
    ]
    assert fake.terminate_calls == ["claude-sess"]
    assert fake.inputs == [("claude-sess", "retry please")]
    assert updated.status == SessionStatus.RUNNING


@pytest.mark.asyncio
async def test_reattach_terminates_before_restoring_codex(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)

    # Order-tracking fake: a stale stream emitting ERROR leaves the adapter's
    # in-memory state in place, so restore must explicitly tear it down before
    # respawning. Capture both calls in a single timeline to assert ordering.
    class TimelineFake(FakeCodexRuntimeAdapter):
        def __init__(self, log: list[str]) -> None:
            super().__init__()
            self._log = log

        async def terminate_session(self, session_id: str) -> bool:
            self._log.append(f"terminate:{session_id}")
            return await super().terminate_session(session_id)

        async def restore_session(
            self,
            session_id: str,
            cwd: str,
            thread_id: str,
            client_factory_override: Any = None,
            model: str | None = None,
            effort: str | None = None,
        ) -> None:
            self._log.append(f"restore:{session_id}")
            await super().restore_session(
                session_id, cwd, thread_id, client_factory_override, model, effort
            )

    timeline: list[str] = []
    fake = TimelineFake(timeline)
    runtime.codex = cast(Any, fake)
    session = make_session(
        settings,
        status=SessionStatus.ERROR,
        thread_id="thread-resume",
    )
    storage.create_session(session)

    await runtime.handle_input("sess", SessionInputRequest(text="back online"))

    assert timeline == ["terminate:sess", "restore:sess"]


@pytest.mark.asyncio
async def test_handle_input_rejects_reattach_for_tmux_session(tmp_path) -> None:
    from fastapi import HTTPException

    runtime, storage, settings = make_runtime(tmp_path)
    session = make_session(
        settings,
        id="tmux-sess",
        backend=Backend.CLAUDE_CODE,
        transport=SessionTransport.TMUX,
        status=SessionStatus.EXITED,
        thread_id=None,
    )
    storage.create_session(session)

    with pytest.raises(HTTPException) as exc:
        await runtime.handle_input(
            "tmux-sess", SessionInputRequest(text="anyone home?")
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_handle_input_rejects_reattach_when_thread_id_missing(tmp_path) -> None:
    from fastapi import HTTPException

    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    runtime.codex = cast(Any, fake)
    session = make_session(
        settings,
        status=SessionStatus.EXITED,
        thread_id=None,
    )
    storage.create_session(session)

    # _restore_codex_session refuses to reattach without a thread id and tags
    # the session EXITED again; the handler surfaces that as 400 instead of
    # silently spawning a fresh thread.
    with pytest.raises(HTTPException) as exc:
        await runtime.handle_input("sess", SessionInputRequest(text="hi"))
    assert exc.value.status_code == 400
    assert fake.restore_calls == []


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
                default_cwd="~/workspace",
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
            cwd="~/workspace",
            launch_target_id="devbox",
            title=None,
            args=[],
            source_mode=SessionSource.MANAGED,
        )
    )

    assert session.transport == SessionTransport.CLAUDE_CLI
    assert session.launch_target_id == "devbox"
    assert session.cwd == "~/workspace"
    assert fake.start_calls == [
        (
            session.id,
            "~/workspace",
            session.thread_id,
            "remote-launch-factory",
            "default",
            None,
            None,
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
                default_cwd="~/workspace",
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
    assert session.launch_target_id == "devbox"
    assert session.repo_name == "project"
    assert session.branch == "main"
    assert fake.restore_calls == [
        (
            session.id,
            "/srv/worktree/project",
            "thread-9",
            "remote-factory",
            None,
            None,
        )
    ]
    events = storage.list_events(session.id)
    assert events[-1].kind == EventKind.SYSTEM_NOTE
    assert "Imported stored Codex thread via SSH target Devbox" in events[-1].text


def _make_claude_thread_info(**overrides: Any) -> ClaudeThreadInfo:
    now = datetime.now(UTC)
    return ClaudeThreadInfo(
        id=overrides.pop("id", "11111111-1111-4111-8111-111111111111"),
        cwd=overrides.pop("cwd", "/tmp/project"),
        title=overrides.pop("title", "Investigation"),
        branch=overrides.pop("branch", "main"),
        repo_name=overrides.pop("repo_name", "project"),
        preview=overrides.pop("preview", "Pick up where we left off"),
        created_at=overrides.pop("created_at", now),
        updated_at=overrides.pop("updated_at", now),
    )


@pytest.mark.asyncio
async def test_list_importable_claude_threads_filters_existing_session(
    monkeypatch, tmp_path
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    runtime.claude = cast(Any, FakeClaudeAdapter())
    storage.create_session(
        make_session(
            settings,
            id="claude-existing",
            backend=Backend.CLAUDE_CODE,
            transport=SessionTransport.CLAUDE_CLI,
            thread_id="11111111-1111-4111-8111-111111111111",
        )
    )
    info_existing = _make_claude_thread_info(
        id="11111111-1111-4111-8111-111111111111", title="Existing"
    )
    info_new = _make_claude_thread_info(
        id="22222222-2222-4222-8222-222222222222",
        title="Fresh thread",
        preview="Hello",
        cwd="/tmp/other-project",
        repo_name="other-project",
        branch="feature/new",
    )

    monkeypatch.setattr(
        "waypoint.runtime.list_local_claude_threads",
        lambda: [info_existing, info_new],
    )

    threads = await runtime.list_importable_claude_threads()

    assert [thread.id for thread in threads] == ["22222222-2222-4222-8222-222222222222"]
    assert threads[0].title == "Fresh thread"
    assert threads[0].cwd == "/tmp/other-project"
    assert threads[0].branch == "feature/new"


class FakeRemoteEnumerator:
    def __init__(self, infos: list[ClaudeThreadInfo] | None = None) -> None:
        self._infos = infos or []
        self.list_calls: list[str] = []
        self.find_calls: list[tuple[str, str]] = []
        self.invalidate_calls: list[str] = []

    async def list(self, target: SshLaunchTargetConfig) -> list[ClaudeThreadInfo]:
        self.list_calls.append(target.id)
        return list(self._infos)

    async def find(
        self, target: SshLaunchTargetConfig, thread_id: str
    ) -> ClaudeThreadInfo | None:
        self.find_calls.append((target.id, thread_id))
        for info in self._infos:
            if info.id == thread_id:
                return info
        return None

    def invalidate(self, launch_target_id: str) -> None:
        self.invalidate_calls.append(launch_target_id)


@pytest.mark.asyncio
async def test_list_importable_claude_threads_remote_target_uses_enumerator(
    tmp_path,
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        ssh_targets=[
            SshLaunchTargetConfig(
                id="devbox",
                name="Devbox",
                ssh_destination="dev@example.com",
                supported_backends=[Backend.CLAUDE_CODE],
                default_cwd="~/workspace",
            )
        ],
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    runtime.claude = cast(Any, FakeClaudeAdapter())
    info = _make_claude_thread_info(
        id="11111111-1111-4111-8111-111111111111",
        title="Remote thread",
        cwd="/srv/project",
        branch="main",
    )
    fake_enum = FakeRemoteEnumerator([info])
    runtime.claude_thread_enumerator = cast(Any, fake_enum)

    summaries = await runtime.list_importable_claude_threads("devbox")

    assert fake_enum.list_calls == ["devbox"]
    assert [s.id for s in summaries] == [info.id]
    assert summaries[0].cwd == "/srv/project"


@pytest.mark.asyncio
async def test_list_importable_claude_threads_dedupes_by_target_and_thread(
    tmp_path,
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        ssh_targets=[
            SshLaunchTargetConfig(
                id="devbox",
                name="Devbox",
                ssh_destination="dev@example.com",
                supported_backends=[Backend.CLAUDE_CODE],
                default_cwd="~/workspace",
            )
        ],
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    runtime.claude = cast(Any, FakeClaudeAdapter())
    # An imported session for the SAME thread_id but no launch target
    # should not hide the remote thread, since they are scoped separately.
    storage.create_session(
        make_session(
            settings,
            id="local-claude",
            backend=Backend.CLAUDE_CODE,
            transport=SessionTransport.CLAUDE_CLI,
            thread_id="11111111-1111-4111-8111-111111111111",
        )
    )
    info = _make_claude_thread_info(id="11111111-1111-4111-8111-111111111111")
    runtime.claude_thread_enumerator = cast(Any, FakeRemoteEnumerator([info]))

    summaries = await runtime.list_importable_claude_threads("devbox")
    assert [s.id for s in summaries] == [info.id]


@pytest.mark.asyncio
async def test_import_claude_thread_creates_session_and_resumes(
    monkeypatch, tmp_path
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    runtime.claude = cast(Any, fake)
    info = _make_claude_thread_info(
        id="33333333-3333-4333-8333-333333333333",
        cwd=str(tmp_path),
        title="Resumed thread",
        branch="main",
        repo_name=tmp_path.name,
        preview="Pick up where we left off",
    )

    monkeypatch.setattr(
        "waypoint.runtime.find_local_claude_thread",
        lambda thread_id: info if thread_id == info.id else None,
    )

    session = await runtime.import_claude_thread(
        ClaudeThreadImportRequest(thread_id=info.id)
    )

    assert session.transport == SessionTransport.CLAUDE_CLI
    assert session.backend == Backend.CLAUDE_CODE
    assert session.thread_id == info.id
    assert session.cwd == str(tmp_path)
    assert session.branch == "main"
    assert session.status == SessionStatus.IDLE
    assert fake.restore_calls == [
        (
            session.id,
            str(tmp_path),
            info.id,
            None,
            "default",
            None,
            None,
        )
    ]
    events = storage.list_events(session.id)
    assert events[-1].kind == EventKind.SYSTEM_NOTE
    assert "Imported stored Claude thread" in events[-1].text


@pytest.mark.asyncio
async def test_import_claude_thread_remote_target_uses_remote_factory(
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
                default_cwd="~/workspace",
            )
        ],
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    fake_claude = FakeClaudeAdapter()
    runtime.claude = cast(Any, fake_claude)
    info = _make_claude_thread_info(
        id="44444444-4444-4444-8444-444444444444",
        cwd="/srv/work",
        title="Remote pickup",
        branch="feature/x",
        repo_name="work",
        preview="resume me",
    )
    fake_enum = FakeRemoteEnumerator([info])
    runtime.claude_thread_enumerator = cast(Any, fake_enum)
    monkeypatch.setattr(
        runtime,
        "_claude_launch_factory",
        lambda launch_target_id: f"remote-factory-{launch_target_id}",
    )

    session = await runtime.import_claude_thread(
        ClaudeThreadImportRequest(thread_id=info.id, launch_target_id="devbox")
    )

    assert session.launch_target_id == "devbox"
    assert session.cwd == "/srv/work"
    assert session.thread_id == info.id
    assert fake_claude.restore_calls == [
        (
            session.id,
            "/srv/work",
            info.id,
            "remote-factory-devbox",
            "default",
            None,
            None,
        )
    ]
    assert fake_enum.invalidate_calls == ["devbox"]
    events = storage.list_events(session.id)
    assert "Imported stored Claude thread via SSH target Devbox" in events[-1].text


@pytest.mark.asyncio
async def test_find_imported_claude_session_scopes_by_launch_target(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    same_thread_id = "abcd1234-5678-4abc-8def-0123456789ab"
    storage.create_session(
        make_session(
            settings,
            id="local-sess",
            backend=Backend.CLAUDE_CODE,
            transport=SessionTransport.CLAUDE_CLI,
            thread_id=same_thread_id,
        )
    )

    # Local match
    found_local = runtime._find_imported_claude_session(same_thread_id, None)
    assert found_local is not None
    assert found_local.id == "local-sess"

    # Same thread_id under a remote target should NOT collide with the
    # local one — different scope.
    assert runtime._find_imported_claude_session(same_thread_id, "devbox") is None


@pytest.mark.asyncio
async def test_delete_remote_claude_session_invalidates_enumerator_cache(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake_enum = FakeRemoteEnumerator()
    runtime.claude_thread_enumerator = cast(Any, fake_enum)
    runtime.claude = cast(Any, FakeClaudeAdapter())
    storage.create_session(
        make_session(
            settings,
            id="remote-claude",
            backend=Backend.CLAUDE_CODE,
            transport=SessionTransport.CLAUDE_CLI,
            status=SessionStatus.EXITED,
            thread_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            launch_target_id="devbox",
        )
    )

    await runtime.delete("remote-claude")

    assert fake_enum.invalidate_calls == ["devbox"]


@pytest.mark.asyncio
async def test_import_claude_thread_missing_returns_404(monkeypatch, tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    runtime.claude = cast(Any, FakeClaudeAdapter())
    monkeypatch.setattr(
        "waypoint.runtime.find_local_claude_thread", lambda _thread_id: None
    )

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await runtime.import_claude_thread(
            ClaudeThreadImportRequest(thread_id="11111111-1111-4111-8111-111111111111")
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_set_permission_mode_codex_persists_and_threads_to_next_turn(
    tmp_path,
) -> None:
    from waypoint.transports.codex import CODEX_PERMISSION_PRESETS

    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter()
    runtime.codex = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)

    updated = await runtime.set_permission_mode("sess", "auto_review")

    # Codex applies the preset on the next turn — no protocol round-trip yet,
    # just persisted on the session record.
    assert updated.permission_mode == "auto_review"
    assert fake.inputs == []

    await runtime.handle_input("sess", SessionInputRequest(text="hello"))
    assert fake.inputs == [("sess", "hello")]
    [(_, params)] = fake.turn_params_calls
    assert params == CODEX_PERMISSION_PRESETS["auto_review"]


@pytest.mark.asyncio
async def test_set_permission_mode_codex_rejects_unknown_mode(tmp_path) -> None:
    from fastapi import HTTPException

    runtime, storage, settings = make_runtime(tmp_path)
    runtime.codex = cast(Any, FakeStructuredAdapter())
    session = make_session(settings)
    storage.create_session(session)

    with pytest.raises(HTTPException) as exc:
        await runtime.set_permission_mode("sess", "unknown_mode")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_set_permission_mode_claude_calls_adapter(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    runtime.claude = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend=Backend.CLAUDE_CODE,
        transport=SessionTransport.CLAUDE_CLI,
    )
    storage.create_session(session)

    updated = await runtime.set_permission_mode("claude-sess", "plan")

    assert fake.permission_mode_calls == [("claude-sess", "plan")]
    assert updated.permission_mode == "plan"


@pytest.mark.asyncio
async def test_approve_syncs_storage_when_adapter_flips_mode(tmp_path) -> None:
    """When ExitPlanMode is approved the Claude adapter sends
    set_permission_mode default to the binary; runtime.approve must mirror
    that into storage so the UI pill updates."""
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    fake.modes["claude-sess"] = "default"  # pretend the adapter flipped already
    runtime.claude = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend=Backend.CLAUDE_CODE,
        transport=SessionTransport.CLAUDE_CLI,
        permission_mode="plan",
    )
    storage.create_session(session)

    await runtime.approve(
        "claude-sess",
        SessionApprovalRequest(decision="accept"),
    )

    refreshed = storage.get_session("claude-sess")
    assert refreshed is not None
    assert refreshed.permission_mode == "default"


@pytest.mark.asyncio
async def test_set_permission_mode_claude_rejects_unknown_mode(tmp_path) -> None:
    from fastapi import HTTPException

    runtime, storage, settings = make_runtime(tmp_path)
    runtime.claude = cast(Any, FakeClaudeAdapter())
    session = make_session(
        settings,
        id="claude-sess",
        backend=Backend.CLAUDE_CODE,
        transport=SessionTransport.CLAUDE_CLI,
    )
    storage.create_session(session)

    with pytest.raises(HTTPException) as exc:
        await runtime.set_permission_mode("claude-sess", "ultraplan")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_set_model_claude_calls_adapter_and_persists(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    runtime.claude = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend=Backend.CLAUDE_CODE,
        transport=SessionTransport.CLAUDE_CLI,
    )
    storage.create_session(session)

    updated = await runtime.set_model("claude-sess", "opus")

    assert fake.model_calls == [("claude-sess", "opus")]
    assert updated.model == "opus"

    # Empty / whitespace strings revert to default — adapter sees None.
    cleared = await runtime.set_model("claude-sess", "  ")
    assert fake.model_calls[-1] == ("claude-sess", None)
    assert cleared.model is None


@pytest.mark.asyncio
async def test_set_model_codex_calls_adapter_and_persists(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    runtime.codex = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)

    updated = await runtime.set_model("sess", "gpt-5")

    assert fake.model_calls == [("sess", "gpt-5")]
    assert updated.model == "gpt-5"


@pytest.mark.asyncio
async def test_list_backend_models_returns_curated_claude_list(tmp_path) -> None:
    runtime, _, settings = make_runtime(tmp_path)
    response = await runtime.list_backend_models(Backend.CLAUDE_CODE)

    assert response["backend"] == Backend.CLAUDE_CODE.value
    assert response["supports_free_text"] is True
    ids = [entry["id"] for entry in response["models"]]
    # Mirrors DEFAULT_CLAUDE_MODELS in config.py.
    assert "opus" in ids and "sonnet" in ids and "haiku" in ids
    # Default falls back to the entry flagged is_default in the curated list
    # when no settings.default_models override is present.
    assert response["default_model"] == "sonnet"


@pytest.mark.asyncio
async def test_list_backend_models_honours_default_models_override(tmp_path) -> None:
    runtime, _, settings = make_runtime(tmp_path)
    settings.default_models = {Backend.CLAUDE_CODE.value: "opus"}
    response = await runtime.list_backend_models(Backend.CLAUDE_CODE)
    assert response["default_model"] == "opus"
