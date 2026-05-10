import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from waypoint.backends.claude_code.permission_modes import CLAUDE_AUTO_APPROVE_MODES
from waypoint.backends.claude_code.schemas import ClaudeThreadImportRequest
from waypoint.backends.claude_code.threads import ClaudeThreadInfo
from waypoint.backends.codex.permission_modes import (
    codex_mode_developer_instructions,
)
from waypoint.backends.codex.schemas import CodexThreadImportRequest
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    CommandCompletion,
    CompletionDispatch,
    EventKind,
    EventRecord,
    SessionApprovalRequest,
    SessionCreateRequest,
    SessionInputRequest,
    SessionPlanApprovalRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


def _claude_plugin(runtime: SessionRuntime) -> Any:
    # Tests reach into the plugin's adapter / hook / thread_enumerator
    # attributes to inject fakes after setup. Those fields aren't on the
    # ``BackendPlugin`` Protocol (they're concrete to the Claude plugin),
    # so we cast through ``Any`` rather than thread a per-test type
    # parameter for what amounts to a test-double seam.
    return runtime.registry.get("claude_code")


def _codex_plugin(runtime: SessionRuntime) -> Any:
    return runtime.registry.get("codex")


class FakeStructuredAdapter:
    def __init__(self, pending: bool = False) -> None:
        self.pending = pending
        self.inputs: list[tuple[str, str]] = []
        self.input_items: list[tuple[str, list[dict[str, Any]]]] = []
        self.turn_params_calls: list[tuple[str, dict[str, Any] | None]] = []
        self.input_items_turn_params_calls: list[tuple[str, dict[str, Any] | None]] = []
        self.approval_calls: list[tuple[str, str]] = []

    async def send_input(
        self, session_id: str, text: str, turn_params: dict[str, Any] | None = None
    ) -> None:
        self.inputs.append((session_id, text))
        self.turn_params_calls.append((session_id, turn_params))

    async def send_input_items(
        self,
        session_id: str,
        items: list[dict[str, Any]],
        turn_params: dict[str, Any] | None = None,
    ) -> None:
        self.input_items.append((session_id, items))
        self.input_items_turn_params_calls.append((session_id, turn_params))

    def has_pending_approval(self, session_id: str) -> bool:
        return self.pending

    async def respond_to_approval(
        self,
        session_id: str,
        decision: str,
        text: str | None = None,
        approval_id: str | None = None,
    ) -> bool:
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
        self.slash_commands: dict[str, tuple[str, ...]] = {}
        self.pending_ids: list[str] = []

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
        custom_args: list[str] | None = None,
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
        custom_args: list[str] | None = None,
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
        if mode in CLAUDE_AUTO_APPROVE_MODES:
            self.pending_ids.clear()

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

    def session_slash_commands(self, session_id: str) -> tuple[str, ...]:
        return self.slash_commands.get(session_id, ())

    def pending_approval_ids(self, session_id: str) -> tuple[str, ...]:
        return tuple(self.pending_ids)


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
        custom_args: list[str] | None = None,
        config_overrides: list[str] | None = None,
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
    transport_state = dict(overrides.get("transport_state", {}))
    if "thread_id" in overrides:
        thread_id = overrides["thread_id"]
        if thread_id is None:
            transport_state.pop("thread_id", None)
        else:
            transport_state["thread_id"] = thread_id
    elif "thread_id" not in transport_state:
        transport_state["thread_id"] = "thread-1"
    return SessionRecord(
        id=overrides.get("id", "sess"),
        backend=overrides.get("backend", "codex"),
        source=SessionSource.MANAGED,
        transport=overrides.get("transport", "codex_app_server"),
        title="Session",
        cwd="/tmp/project",
        launch_target_id=overrides.get("launch_target_id"),
        status=overrides.get("status", SessionStatus.IDLE),
        created_at=now,
        updated_at=now,
        last_event_at=now,
        transport_state=transport_state,
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
async def test_list_command_completions_uses_backend_static_slash_commands(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    session = make_session(
        settings,
        backend="opencode",
        transport="opencode_http",
        transport_state={"thread_id": "opencode-session"},
    )
    storage.create_session(session)

    completions = await runtime.list_command_completions(
        session.id, trigger="/", prefix="/co"
    )

    assert [item.name for item in completions] == ["compact"]
    assert completions[0].replacement == "/compact "
    assert completions[0].dispatch == CompletionDispatch.PLAIN_TEXT


@pytest.mark.asyncio
async def test_list_command_completions_uses_claude_runtime_commands(
    monkeypatch, tmp_path
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    fake.slash_commands["claude-sess"] = ("clear", "compact", "usage")
    _claude_plugin(runtime).adapter = cast(Any, fake)

    async def fake_dynamic_completions(**_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.list_claude_command_completions",
        fake_dynamic_completions,
    )
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
        thread_id="claude-thread",
    )
    storage.create_session(session)

    completions = await runtime.list_command_completions(
        session.id, trigger="/", prefix="/us", force_refresh=True
    )

    assert [item.name for item in completions] == ["usage"]
    assert completions[0].replacement == "/usage "
    assert completions[0].dispatch == CompletionDispatch.PLAIN_TEXT
    assert completions[0].source == "claude_builtin"


@pytest.mark.asyncio
async def test_list_command_completions_claude_omits_unreported_tui_commands(
    monkeypatch, tmp_path
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    fake.slash_commands["claude-sess"] = ("clear", "compact", "usage")
    _claude_plugin(runtime).adapter = cast(Any, fake)

    async def fake_dynamic_completions(**_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.list_claude_command_completions",
        fake_dynamic_completions,
    )
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
        thread_id="claude-thread",
    )
    storage.create_session(session)

    completions = await runtime.list_command_completions(
        session.id, trigger="/", prefix="/", force_refresh=True
    )

    names = {item.name for item in completions}
    assert "status" in names
    assert "usage" in names
    assert "permissions" not in names
    assert "help" not in names


@pytest.mark.asyncio
async def test_list_command_completions_claude_uses_stored_init_commands(
    monkeypatch, tmp_path
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    _claude_plugin(runtime).adapter = cast(Any, fake)

    async def fake_dynamic_completions(**_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.list_claude_command_completions",
        fake_dynamic_completions,
    )
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
        thread_id="claude-thread",
    )
    storage.create_session(session)
    storage.append_event(
        EventRecord(
            session_id=session.id,
            ts=datetime.now(UTC),
            kind=EventKind.SYSTEM_NOTE,
            text="Claude session ready",
            metadata={
                "method": "system.init",
                "payload": {"slash_commands": ["clear", "compact", "usage"]},
                "status": SessionStatus.RUNNING,
            },
            sequence=storage.next_sequence(session.id),
        )
    )
    await _claude_plugin(runtime).restore_session(runtime, session)
    session = runtime.get_session(session.id)

    completions = await runtime.list_command_completions(
        session.id, trigger="/", prefix="/us", force_refresh=True
    )

    assert [item.name for item in completions] == ["usage"]


@pytest.mark.asyncio
async def test_list_command_completions_claude_merges_dynamic_descriptions(
    monkeypatch, tmp_path
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    fake.slash_commands["claude-sess"] = ("frontend-design:frontend-design",)
    _claude_plugin(runtime).adapter = cast(Any, fake)

    async def fake_dynamic_completions(**_kwargs: Any) -> list[CommandCompletion]:
        return [
            CommandCompletion(
                id="claude_code:plugin_skill:frontend-design",
                trigger="/",
                replacement="/frontend-design ",
                name="frontend-design",
                description="Design polished frontend interfaces",
                kind="skill",
                source="plugin_skill",
                dispatch=CompletionDispatch.PLAIN_TEXT,
                metadata={"path": "/skills/frontend-design/SKILL.md"},
            )
        ]

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.list_claude_command_completions",
        fake_dynamic_completions,
    )
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
        thread_id="claude-thread",
    )
    storage.create_session(session)

    completions = await runtime.list_command_completions(
        session.id, trigger="/", prefix="/front", force_refresh=True
    )

    assert [item.name for item in completions] == ["frontend-design"]
    assert completions[0].description == "Design polished frontend interfaces"
    assert completions[0].metadata["path"] == "/skills/frontend-design/SKILL.md"


@pytest.mark.asyncio
async def test_emit_adapter_event_refreshes_completion_cache(
    monkeypatch, tmp_path
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    fake.slash_commands["claude-sess"] = ("clear", "compact", "usage")
    _claude_plugin(runtime).adapter = cast(Any, fake)

    async def fake_dynamic_completions(**_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.list_claude_command_completions",
        fake_dynamic_completions,
    )
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
        thread_id="claude-thread",
    )
    storage.create_session(session)
    runtime._completion_cache[(session.id, "/")] = []
    runtime._completion_cache_updated_at[(session.id, "/")] = 1.0

    runtime.handle_completion_source_init(
        session.id,
        {"slash_commands": ["clear", "compact", "usage"]},
    )
    tasks = list(runtime._completion_refresh_tasks.values())
    if tasks:
        await asyncio.gather(*tasks)

    completions = runtime._completion_cache[(session.id, "/")]
    assert "usage" in {item.name for item in completions}


@pytest.mark.asyncio
async def test_list_command_completions_codex_omits_unsupported_legacy_commands(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    session = make_session(settings)
    storage.create_session(session)

    completions = await runtime.list_command_completions(
        session.id, trigger="/", prefix="/"
    )

    names = [item.name for item in completions]
    assert "status" in names
    assert "compact" in names
    assert "plan" in names
    assert "help" not in names
    assert "permissions" not in names


@pytest.mark.asyncio
async def test_list_command_completions_uses_codex_skills(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)

    class FakeAdapter:
        async def list_skills(
            self, session_id: str, *, force_reload: bool = False
        ) -> list[dict[str, Any]]:
            assert session_id == "sess"
            assert force_reload is True
            return [
                {
                    "name": "humanizer",
                    "description": "Humanize prose",
                    "path": "/tmp/SKILL.md",
                }
            ]

    _codex_plugin(runtime).adapter = cast(Any, FakeAdapter())
    session = make_session(settings)
    storage.create_session(session)

    completions = await runtime.list_command_completions(
        session.id, trigger="$", prefix="$hum", force_refresh=True
    )

    assert [item.name for item in completions] == ["humanizer"]
    assert completions[0].replacement == "$humanizer "
    assert completions[0].dispatch == CompletionDispatch.STRUCTURED_SKILL


@pytest.mark.asyncio
async def test_handle_input_uses_server_completion_metadata_for_codex_skill(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter()
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)
    runtime._completion_cache[(session.id, "$")] = [
        CommandCompletion(
            id="codex:skill:humanizer",
            trigger="$",
            replacement="$humanizer ",
            name="humanizer",
            description="Humanize prose",
            kind="skill",
            source="codex_skill",
            dispatch=CompletionDispatch.STRUCTURED_SKILL,
            metadata={"path": "/trusted/SKILL.md"},
        )
    ]
    request = SessionInputRequest(
        text="$humanizer please",
        command={
            "completion_id": "codex:skill:humanizer",
            "name": "humanizer",
            "arguments": "please",
            "dispatch": "structured_skill",
            "metadata": {"path": "/attacker/SKILL.md"},
        },
    )

    await runtime.handle_input(session.id, request)

    assert fake.input_items == [
        (
            session.id,
            [
                {"type": "skill", "name": "humanizer", "path": "/trusted/SKILL.md"},
                {"type": "text", "text": "please"},
            ],
        )
    ]


@pytest.mark.asyncio
async def test_handle_input_drops_unknown_completion_invocation(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter()
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)
    request = SessionInputRequest(
        text="$humanizer please",
        command={
            "completion_id": "codex:skill:missing",
            "name": "humanizer",
            "arguments": "please",
            "dispatch": "structured_skill",
            "metadata": {"path": "/attacker/SKILL.md"},
        },
    )

    await runtime.handle_input(session.id, request)

    assert fake.input_items == []
    assert fake.inputs == [(session.id, "$humanizer please")]


@pytest.mark.asyncio
async def test_get_command_completions_returns_cache_while_refreshing(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)

    class FakeAdapter:
        async def list_skills(
            self, session_id: str, *, force_reload: bool = False
        ) -> list[dict[str, Any]]:
            assert session_id == "sess"
            assert force_reload is True
            return [
                {
                    "name": "humanizer",
                    "description": "Humanize prose",
                    "path": "/tmp/SKILL.md",
                }
            ]

    _codex_plugin(runtime).adapter = cast(Any, FakeAdapter())
    session = make_session(settings)
    storage.create_session(session)

    completions, refreshing = await runtime.get_command_completions(
        session.id, trigger="$", prefix="$hum"
    )

    assert completions == []
    assert refreshing is True
    await asyncio.gather(*runtime._completion_refresh_tasks.values())

    completions, refreshing = await runtime.get_command_completions(
        session.id, trigger="$", prefix="$hum"
    )

    assert [item.name for item in completions] == ["humanizer"]
    assert refreshing is False


@pytest.mark.asyncio
async def test_handle_input_reattaches_exited_codex_session(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    _codex_plugin(runtime).adapter = cast(Any, fake)
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
    _claude_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
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
            custom_args: list[str] | None = None,
            config_overrides: list[str] | None = None,
        ) -> None:
            self._log.append(f"restore:{session_id}")
            await super().restore_session(
                session_id, cwd, thread_id, client_factory_override, model, effort
            )

    timeline: list[str] = []
    fake = TimelineFake(timeline)
    _codex_plugin(runtime).adapter = cast(Any, fake)
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
        backend="claude_code",
        transport="tmux",
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
    _codex_plugin(runtime).adapter = cast(Any, fake)
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
async def test_handle_input_status_renders_codex_status(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter()
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)

    await runtime.handle_input("sess", SessionInputRequest(text="/status"))

    assert fake.inputs == []
    events = storage.list_events("sess")
    assert [event.kind for event in events] == [
        EventKind.USER_INPUT,
        EventKind.SYSTEM_NOTE,
    ]
    assert events[0].text == "/status"
    assert events[0].metadata["status"] == "idle"
    assert "Codex session status" in events[1].text
    assert "- Status: idle" in events[1].text
    assert events[1].metadata["builtin_command"] == "/status"


@pytest.mark.asyncio
async def test_handle_input_status_renders_opencode_status(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    session = make_session(
        settings,
        id="opencode-sess",
        backend="opencode",
        transport="opencode_http",
        transport_state={"opencode_session_id": "oc-session"},
        permission_mode="default",
    )
    storage.create_session(session)

    await runtime.handle_input("opencode-sess", SessionInputRequest(text="/status"))

    events = storage.list_events("opencode-sess")
    assert [event.kind for event in events] == [
        EventKind.USER_INPUT,
        EventKind.SYSTEM_NOTE,
    ]
    assert "OpenCode session status" in events[1].text
    assert "- Thread: oc-session" in events[1].text
    assert events[1].metadata["builtin_command"] == "/status"
    assert events[1].metadata["source"] == "waypoint"


@pytest.mark.asyncio
async def test_handle_input_status_renders_claude_waypoint_status(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    fake.slash_commands["claude-sess"] = ("clear", "compact", "usage")
    _claude_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
        thread_id="claude-thread",
        permission_mode="default",
    )
    storage.create_session(session)

    await runtime.handle_input("claude-sess", SessionInputRequest(text="/status"))

    assert fake.inputs == []
    events = storage.list_events("claude-sess")
    assert [event.kind for event in events] == [
        EventKind.USER_INPUT,
        EventKind.SYSTEM_NOTE,
    ]
    assert "Claude Code session status" in events[1].text
    assert "- Runtime slash commands: /clear, /compact, /usage" in events[1].text
    assert events[1].metadata["builtin_command"] == "/status"
    assert events[1].metadata["source"] == "waypoint"


@pytest.mark.asyncio
async def test_handle_input_status_forwards_when_claude_reports_native_status(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    fake.slash_commands["claude-sess"] = ("status",)
    _claude_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
        thread_id="claude-thread",
    )
    storage.create_session(session)

    await runtime.handle_input("claude-sess", SessionInputRequest(text="/status"))

    assert fake.inputs == [("claude-sess", "/status")]


@pytest.mark.asyncio
async def test_handle_input_records_user_event_before_send(tmp_path) -> None:
    # OpenCode's HTTP POST returns only after SSE has already pushed turn
    # events; if send_input ran first, the user message would land last in
    # the transcript. The contract: the user_event is in storage by the time
    # the transport sees the input.
    runtime, storage, settings = make_runtime(tmp_path)

    class OrderingAdapter(FakeStructuredAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.events_at_send: list[EventKind] = []

        async def send_input(
            self,
            session_id: str,
            text: str,
            turn_params: dict[str, Any] | None = None,
        ) -> None:
            self.events_at_send = [
                event.kind for event in storage.list_events(session_id)
            ]
            await super().send_input(session_id, text, turn_params)

    fake = OrderingAdapter()
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)

    await runtime.handle_input("sess", SessionInputRequest(text="hello"))

    assert fake.events_at_send == [EventKind.USER_INPUT]
    assert fake.inputs == [("sess", "hello")]


@pytest.mark.asyncio
async def test_handle_input_reverts_status_when_send_fails(tmp_path) -> None:
    # The status flip happens before send_input now; if the transport raises,
    # the runtime must roll the status back so the UI doesn't show "running"
    # for an unsent message.
    runtime, storage, settings = make_runtime(tmp_path)

    class FailingAdapter(FakeStructuredAdapter):
        async def send_input(
            self,
            session_id: str,
            text: str,
            turn_params: dict[str, Any] | None = None,
        ) -> None:
            raise RuntimeError("network down")

    fake = FailingAdapter()
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)
    initial_status = session.status

    # The codex transport wraps adapter errors in HTTPException; we just
    # care that the runtime propagates rather than swallowing it.
    with pytest.raises(Exception, match="network down"):
        await runtime.handle_input("sess", SessionInputRequest(text="hello"))

    reloaded = storage.get_session("sess")
    assert reloaded is not None
    assert reloaded.status == initial_status


@pytest.mark.asyncio
async def test_handle_input_permissions_forwards_to_claude_cli(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter(pending=True)
    _claude_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
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
    _claude_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
    )
    storage.create_session(session)

    await runtime.handle_input("claude-sess", SessionInputRequest(text="/help"))

    assert fake.inputs == [("claude-sess", "/help")]


@pytest.mark.asyncio
async def test_handle_input_builtin_compact_forwards_to_claude_cli(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter()
    _claude_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
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
    _codex_plugin(runtime).adapter = cast(Any, fake)
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
async def test_handle_input_builtin_plan_switches_codex_plan_mode_only(
    tmp_path,
) -> None:
    from waypoint.backends.codex.permission_modes import CODEX_PERMISSION_PRESETS

    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    fake.models["sess"] = "gpt-5.3-codex"
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(settings, permission_mode="full_access")
    storage.create_session(session)

    updated = await runtime.handle_input("sess", SessionInputRequest(text="/plan"))

    assert updated.permission_mode == "plan"
    assert runtime.get_session("sess").transport_state["pre_plan_mode"] == "full_access"
    assert fake.inputs == []
    events = storage.list_events("sess")
    assert [event.kind for event in events] == [
        EventKind.USER_INPUT,
        EventKind.SYSTEM_NOTE,
    ]
    assert events[-1].metadata["builtin_command"] == "/plan"
    assert "plan mode" in events[-1].text

    await runtime.handle_input("sess", SessionInputRequest(text="draft the plan"))

    assert fake.inputs == [("sess", "draft the plan")]
    [(_, params)] = fake.turn_params_calls
    assert params == {
        **CODEX_PERMISSION_PRESETS["full_access"],
        "collaborationMode": {
            "mode": "plan",
            "settings": {
                "model": "gpt-5.3-codex",
                "reasoning_effort": "medium",
                "developer_instructions": codex_mode_developer_instructions("plan"),
            },
        },
    }


@pytest.mark.asyncio
async def test_handle_input_builtin_plan_with_prompt_starts_plan_turn(
    tmp_path,
) -> None:
    from waypoint.backends.codex.permission_modes import CODEX_PERMISSION_PRESETS

    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    fake.models["sess"] = "gpt-5.3-codex"
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(settings, permission_mode="full_access")
    storage.create_session(session)

    updated = await runtime.handle_input(
        "sess", SessionInputRequest(text="/plan design this")
    )

    assert updated.status == SessionStatus.RUNNING
    assert fake.inputs == [("sess", "design this")]
    assert runtime.get_session("sess").permission_mode == "plan"
    assert runtime.get_session("sess").transport_state["pre_plan_mode"] == "full_access"
    [(_, params)] = fake.turn_params_calls
    assert params == {
        **CODEX_PERMISSION_PRESETS["full_access"],
        "collaborationMode": {
            "mode": "plan",
            "settings": {
                "model": "gpt-5.3-codex",
                "reasoning_effort": "medium",
                "developer_instructions": codex_mode_developer_instructions("plan"),
            },
        },
    }
    events = storage.list_events("sess")
    assert len(events) == 1
    assert events[0].kind == EventKind.USER_INPUT
    assert events[0].text == "/plan design this"


@pytest.mark.asyncio
async def test_handle_input_unknown_slash_command_forwards_to_structured_session(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter()
    _codex_plugin(runtime).adapter = cast(Any, fake)
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
                plugin_configs={"claude_code": {}},
                default_cwd="~/workspace",
            )
        ],
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    fake = FakeClaudeAdapter()
    _claude_plugin(runtime).adapter = cast(Any, fake)
    _claude_plugin(runtime).hook = cast(
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
    _claude_plugin(runtime).hook.hook_script_path.write_text(
        "#!/usr/bin/env python3\nprint('hook')\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.build_remote_claude_launch_factory",
        lambda *args, **kwargs: "remote-launch-factory",
    )

    session = await runtime.create_session(
        SessionCreateRequest(
            backend="claude_code",
            cwd="~/workspace",
            launch_target_id="devbox",
            title=None,
            args=[],
            source_mode=SessionSource.MANAGED,
        )
    )

    assert session.transport == "claude_cli"
    assert session.launch_target_id == "devbox"
    assert session.cwd == "~/workspace"
    assert fake.start_calls == [
        (
            session.id,
            "~/workspace",
            session.transport_state["thread_id"],
            "remote-launch-factory",
            "default",
            "opus[1m]",
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

    async def fake_run(_runtime, launch_target_id, operation, **kwargs):
        return [thread_one, thread_two]

    codex_plugin = runtime.registry.get("codex")
    monkeypatch.setattr(codex_plugin, "run_client_operation", fake_run)

    threads = await runtime.registry.get("codex").list_threads(runtime)

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
                plugin_configs={"codex": {}},
                default_cwd="~/workspace",
            )
        ],
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    fake = FakeCodexRuntimeAdapter()
    _codex_plugin(runtime).adapter = cast(Any, fake)
    thread = make_thread(
        id="thread-9",
        name="Existing remote thread",
        cwd=SimpleNamespace(root="/srv/worktree/project"),
        git_info=SimpleNamespace(
            branch="main", origin_url="ssh://git.example.com/team/project.git"
        ),
    )

    async def fake_read(_runtime, thread_id: str, launch_target_id: str | None) -> Any:
        assert thread_id == "thread-9"
        assert launch_target_id == "devbox"
        return thread

    codex_plugin = runtime.registry.get("codex")
    monkeypatch.setattr(codex_plugin, "_read_thread", fake_read)
    monkeypatch.setattr(
        codex_plugin,
        "client_factory",
        lambda _runtime, launch_target_id, **_kwargs: "remote-factory",
    )

    session = await runtime.registry.get("codex").import_thread(
        runtime,
        CodexThreadImportRequest(thread_id="thread-9", launch_target_id="devbox"),
    )

    assert session.transport == "codex_app_server"
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
    _claude_plugin(runtime).adapter = cast(Any, FakeClaudeAdapter())
    storage.create_session(
        make_session(
            settings,
            id="claude-existing",
            backend="claude_code",
            transport="claude_cli",
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
        "waypoint.backends.claude_code.plugin.list_local_claude_threads",
        lambda: [info_existing, info_new],
    )

    threads = await runtime.registry.get("claude_code").list_threads(runtime)

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
                plugin_configs={"claude_code": {}},
                default_cwd="~/workspace",
            )
        ],
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    _claude_plugin(runtime).adapter = cast(Any, FakeClaudeAdapter())
    info = _make_claude_thread_info(
        id="11111111-1111-4111-8111-111111111111",
        title="Remote thread",
        cwd="/srv/project",
        branch="main",
    )
    fake_enum = FakeRemoteEnumerator([info])
    _claude_plugin(runtime).thread_enumerator = cast(Any, fake_enum)

    summaries = await runtime.registry.get("claude_code").list_threads(
        runtime, "devbox"
    )

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
                plugin_configs={"claude_code": {}},
                default_cwd="~/workspace",
            )
        ],
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    _claude_plugin(runtime).adapter = cast(Any, FakeClaudeAdapter())
    # An imported session for the SAME thread_id but no launch target
    # should not hide the remote thread, since they are scoped separately.
    storage.create_session(
        make_session(
            settings,
            id="local-claude",
            backend="claude_code",
            transport="claude_cli",
            thread_id="11111111-1111-4111-8111-111111111111",
        )
    )
    info = _make_claude_thread_info(id="11111111-1111-4111-8111-111111111111")
    _claude_plugin(runtime).thread_enumerator = cast(Any, FakeRemoteEnumerator([info]))

    summaries = await runtime.registry.get("claude_code").list_threads(
        runtime, "devbox"
    )
    assert [s.id for s in summaries] == [info.id]


@pytest.mark.asyncio
async def test_import_claude_thread_creates_session_and_resumes(
    monkeypatch, tmp_path
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    _claude_plugin(runtime).adapter = cast(Any, fake)
    info = _make_claude_thread_info(
        id="33333333-3333-4333-8333-333333333333",
        cwd=str(tmp_path),
        title="Resumed thread",
        branch="main",
        repo_name=tmp_path.name,
        preview="Pick up where we left off",
    )

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.find_local_claude_thread",
        lambda thread_id: info if thread_id == info.id else None,
    )

    session = await runtime.registry.get("claude_code").import_thread(
        runtime, ClaudeThreadImportRequest(thread_id=info.id)
    )

    assert session.transport == "claude_cli"
    assert session.backend == "claude_code"
    assert session.transport_state["thread_id"] == info.id
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
                plugin_configs={"claude_code": {}},
                default_cwd="~/workspace",
            )
        ],
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    fake_claude = FakeClaudeAdapter()
    _claude_plugin(runtime).adapter = cast(Any, fake_claude)
    info = _make_claude_thread_info(
        id="44444444-4444-4444-8444-444444444444",
        cwd="/srv/work",
        title="Remote pickup",
        branch="feature/x",
        repo_name="work",
        preview="resume me",
    )
    fake_enum = FakeRemoteEnumerator([info])
    _claude_plugin(runtime).thread_enumerator = cast(Any, fake_enum)
    claude_plugin = runtime.registry.get("claude_code")
    monkeypatch.setattr(
        claude_plugin,
        "launch_factory",
        lambda _runtime, launch_target_id: f"remote-factory-{launch_target_id}",
    )

    session = await runtime.registry.get("claude_code").import_thread(
        runtime, ClaudeThreadImportRequest(thread_id=info.id, launch_target_id="devbox")
    )

    assert session.launch_target_id == "devbox"
    assert session.cwd == "/srv/work"
    assert session.transport_state["thread_id"] == info.id
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
            backend="claude_code",
            transport="claude_cli",
            thread_id=same_thread_id,
        )
    )

    # Local match
    claude_plugin = cast(Any, runtime.registry.get("claude_code"))
    found_local = claude_plugin._find_imported_session(runtime, same_thread_id, None)
    assert found_local is not None
    assert found_local.id == "local-sess"

    # Same thread_id under a remote target should NOT collide with the
    # local one — different scope.
    assert (
        claude_plugin._find_imported_session(runtime, same_thread_id, "devbox") is None
    )


@pytest.mark.asyncio
async def test_delete_remote_claude_session_invalidates_enumerator_cache(
    tmp_path,
) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake_enum = FakeRemoteEnumerator()
    _claude_plugin(runtime).thread_enumerator = cast(Any, fake_enum)
    _claude_plugin(runtime).adapter = cast(Any, FakeClaudeAdapter())
    storage.create_session(
        make_session(
            settings,
            id="remote-claude",
            backend="claude_code",
            transport="claude_cli",
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
    _claude_plugin(runtime).adapter = cast(Any, FakeClaudeAdapter())
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.find_local_claude_thread",
        lambda _thread_id: None,
    )

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await runtime.registry.get("claude_code").import_thread(
            runtime,
            ClaudeThreadImportRequest(thread_id="11111111-1111-4111-8111-111111111111"),
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_set_permission_mode_codex_persists_and_threads_to_next_turn(
    tmp_path,
) -> None:
    from waypoint.backends.codex.permission_modes import CODEX_PERMISSION_PRESETS

    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    fake.models["sess"] = "gpt-5.3-codex"
    fake.efforts["sess"] = "high"
    _codex_plugin(runtime).adapter = cast(Any, fake)
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
    assert params == {
        **CODEX_PERMISSION_PRESETS["auto_review"],
        "collaborationMode": {
            "mode": "default",
            "settings": {
                "model": "gpt-5.3-codex",
                "reasoning_effort": "high",
                "developer_instructions": codex_mode_developer_instructions("default"),
            },
        },
    }


@pytest.mark.asyncio
async def test_set_permission_mode_codex_plan_preserves_previous_preset(
    tmp_path,
) -> None:
    from waypoint.backends.codex.permission_modes import CODEX_PERMISSION_PRESETS

    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    fake.models["sess"] = "gpt-5.3-codex"
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(settings, permission_mode="auto_review")
    storage.create_session(session)

    updated = await runtime.set_permission_mode("sess", "plan")

    assert updated.permission_mode == "plan"
    assert runtime.get_session("sess").transport_state["pre_plan_mode"] == "auto_review"

    await runtime.handle_input("sess", SessionInputRequest(text="hello"))

    [(_, params)] = fake.turn_params_calls
    assert params == {
        **CODEX_PERMISSION_PRESETS["auto_review"],
        "collaborationMode": {
            "mode": "plan",
            "settings": {
                "model": "gpt-5.3-codex",
                "reasoning_effort": "medium",
                "developer_instructions": codex_mode_developer_instructions("plan"),
            },
        },
    }


@pytest.mark.asyncio
async def test_set_permission_mode_codex_leaving_plan_clears_previous_preset(
    tmp_path,
) -> None:
    from waypoint.backends.codex.permission_modes import CODEX_PERMISSION_PRESETS

    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    fake.models["sess"] = "gpt-5.3-codex"
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        permission_mode="plan",
        transport_state={"thread_id": "thread-1", "pre_plan_mode": "auto_review"},
    )
    storage.create_session(session)

    updated = await runtime.set_permission_mode("sess", "default")

    assert updated.permission_mode == "default"
    assert "pre_plan_mode" not in runtime.get_session("sess").transport_state

    await runtime.handle_input("sess", SessionInputRequest(text="am I still planning?"))

    [(_, params)] = fake.turn_params_calls
    assert params == {
        **CODEX_PERMISSION_PRESETS["default"],
        "collaborationMode": {
            "mode": "default",
            "settings": {
                "model": "gpt-5.3-codex",
                "reasoning_effort": None,
                "developer_instructions": codex_mode_developer_instructions("default"),
            },
        },
    }
    # Sanity: a non-empty body is required for Codex's app-server to
    # emit a collaboration-mode update item; otherwise the previous
    # plan-mode developer instructions linger and the model behaves
    # as if it never left plan mode.
    body = params["collaborationMode"]["settings"]["developer_instructions"]
    assert isinstance(body, str) and "Default" in body
    assert "Plan Mode (Conversational)" not in body


def test_codex_mode_developer_instructions_falls_back_when_templates_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from waypoint.backends.codex import permission_modes

    permission_modes.codex_mode_developer_instructions.cache_clear()
    monkeypatch.setattr(permission_modes, "_load_mode_template", lambda name: None)
    try:
        default_body = permission_modes.codex_mode_developer_instructions("default")
        plan_body = permission_modes.codex_mode_developer_instructions("plan")
    finally:
        permission_modes.codex_mode_developer_instructions.cache_clear()

    assert "Default mode" in default_body
    assert "Plan Mode" in plan_body


@pytest.mark.asyncio
async def test_runtime_fork_codex_session_uses_codex_plugin(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)

    class CodexForkFake(FakeCodexRuntimeAdapter):
        async def fork_session(
            self,
            session_id: str,
            cwd: str,
            thread_id: str,
            client_factory_override: Any = None,
            model: str | None = None,
            effort: str | None = None,
            custom_args: list[str] | None = None,
            config_overrides: list[str] | None = None,
        ) -> str:
            assert session_id.startswith("codex-")
            assert (cwd, thread_id) == ("/tmp/project", "thread-1")
            return "thread-forked"

    plugin = _codex_plugin(runtime)
    plugin.adapter = cast(Any, CodexForkFake())
    session = make_session(settings)
    storage.create_session(session)

    forked = await runtime.fork_session("sess")

    assert forked.backend == "codex"
    assert forked.title == "Session (fork #1)"
    assert forked.transport_state == {"thread_id": "thread-forked"}


@pytest.mark.asyncio
async def test_fork_codex_plan_session_persists_pre_plan_mode(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)

    class CodexForkFake(FakeCodexRuntimeAdapter):
        async def fork_session(
            self,
            session_id: str,
            cwd: str,
            thread_id: str,
            client_factory_override: Any = None,
            model: str | None = None,
            effort: str | None = None,
            custom_args: list[str] | None = None,
            config_overrides: list[str] | None = None,
        ) -> str:
            assert (session_id, cwd, thread_id) == (
                "forked",
                "/tmp/project",
                "thread-1",
            )
            return "thread-forked"

    plugin = _codex_plugin(runtime)
    plugin.adapter = cast(Any, CodexForkFake())
    session = make_session(
        settings,
        permission_mode="plan",
        transport_state={"thread_id": "thread-1", "pre_plan_mode": "full_access"},
    )
    storage.create_session(session)

    forked = await plugin.fork_session(
        runtime,
        session,
        new_session_id="forked",
        title="Forked",
        raw_log=tmp_path / "raw.log",
        structured_log=tmp_path / "events.jsonl",
    )

    assert forked.permission_mode == "plan"
    assert forked.transport_state == {
        "thread_id": "thread-forked",
        "pre_plan_mode": "full_access",
    }


def _seed_plan_event(
    storage: Storage,
    *,
    session_id: str = "sess",
    plan_id: str = "plan-1",
    text: str = "1. Update the UI\n2. Run tests",
) -> None:
    storage.append_event(
        EventRecord(
            session_id=session_id,
            ts=datetime.now(UTC),
            kind=EventKind.SYSTEM_NOTE,
            text="Completed plan",
            metadata={
                "item_id": plan_id,
                "item_type": "plan",
                "plan": {
                    "id": plan_id,
                    "text": text,
                    "source": "codex",
                    "decisions": [
                        "accept",
                        "acceptForSession",
                        "decline",
                        "cancel",
                    ],
                },
                "payload": {
                    "item": {
                        "id": plan_id,
                        "type": "plan",
                        "text": text,
                    }
                },
            },
            sequence=storage.next_sequence(session_id),
        )
    )


@pytest.mark.asyncio
async def test_approve_codex_plan_restores_mode_and_sends_prompt(tmp_path) -> None:
    from waypoint.backends.codex.permission_modes import CODEX_PERMISSION_PRESETS

    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    fake.models["sess"] = "gpt-5.3-codex"
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        permission_mode="plan",
        transport_state={"thread_id": "thread-1", "pre_plan_mode": "full_access"},
    )
    storage.create_session(session)
    _seed_plan_event(storage)

    updated = await runtime.approve_plan(
        "sess",
        SessionPlanApprovalRequest(plan_item_id="plan-1", decision="accept"),
    )

    refreshed = storage.get_session("sess")
    assert refreshed is not None
    assert updated.permission_mode == "full_access"
    assert refreshed.permission_mode == "full_access"
    assert refreshed.status == SessionStatus.RUNNING
    assert "pre_plan_mode" not in refreshed.transport_state
    assert fake.inputs == [
        (
            "sess",
            "User has approved your plan. You can now start coding. "
            "Start with updating your todo list if applicable.\n\n"
            "## Approved Plan:\n"
            "1. Update the UI\n2. Run tests",
        )
    ]
    [(_, params)] = fake.turn_params_calls
    assert params == {
        **CODEX_PERMISSION_PRESETS["full_access"],
        "collaborationMode": {
            "mode": "default",
            "settings": {
                "model": "gpt-5.3-codex",
                "reasoning_effort": None,
                "developer_instructions": codex_mode_developer_instructions("default"),
            },
        },
    }


@pytest.mark.asyncio
async def test_approve_codex_plan_accept_for_session_matches_accept(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        permission_mode="plan",
        transport_state={"thread_id": "thread-1", "pre_plan_mode": "auto_review"},
    )
    storage.create_session(session)
    _seed_plan_event(storage)

    updated = await runtime.approve_plan(
        "sess",
        SessionPlanApprovalRequest(plan_item_id="plan-1", decision="acceptForSession"),
    )

    refreshed = storage.get_session("sess")
    assert refreshed is not None
    assert updated.permission_mode == "auto_review"
    assert refreshed.permission_mode == "auto_review"
    assert "pre_plan_mode" not in refreshed.transport_state
    assert fake.inputs[0][1].startswith("User has approved your plan.")


@pytest.mark.asyncio
async def test_approve_codex_plan_decline_keeps_plan_mode(tmp_path) -> None:
    from waypoint.backends.codex.permission_modes import CODEX_PERMISSION_PRESETS

    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    fake.models["sess"] = "gpt-5.3-codex"
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        permission_mode="plan",
        transport_state={"thread_id": "thread-1", "pre_plan_mode": "full_access"},
    )
    storage.create_session(session)
    _seed_plan_event(storage)

    updated = await runtime.approve_plan(
        "sess",
        SessionPlanApprovalRequest(
            plan_item_id="plan-1", decision="decline", text="prefer option B"
        ),
    )

    refreshed = storage.get_session("sess")
    assert refreshed is not None
    assert updated.permission_mode == "plan"
    assert refreshed.permission_mode == "plan"
    assert refreshed.transport_state["pre_plan_mode"] == "full_access"
    assert refreshed.status == SessionStatus.RUNNING
    [(_, prompt)] = fake.inputs
    assert prompt.startswith("User has declined the plan;")
    assert "prefer option B" in prompt
    [(_, params)] = fake.turn_params_calls
    assert params == {
        **CODEX_PERMISSION_PRESETS["full_access"],
        "collaborationMode": {
            "mode": "plan",
            "settings": {
                "model": "gpt-5.3-codex",
                "reasoning_effort": "medium",
                "developer_instructions": codex_mode_developer_instructions("plan"),
            },
        },
    }
    assert any(
        "Plan declined; staying in plan mode" in event.text
        for event in storage.list_events("sess")
    )


@pytest.mark.asyncio
async def test_approve_codex_plan_cancel_keeps_plan_mode(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    fake.models["sess"] = "gpt-5.3-codex"
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        permission_mode="plan",
        transport_state={"thread_id": "thread-1", "pre_plan_mode": "default"},
    )
    storage.create_session(session)
    _seed_plan_event(storage)

    updated = await runtime.approve_plan(
        "sess",
        SessionPlanApprovalRequest(plan_item_id="plan-1", decision="cancel"),
    )

    refreshed = storage.get_session("sess")
    assert refreshed is not None
    assert updated.permission_mode == "plan"
    assert refreshed.permission_mode == "plan"
    assert refreshed.transport_state["pre_plan_mode"] == "default"
    [(_, prompt)] = fake.inputs
    assert prompt.startswith("User has cancelled the plan;")
    [(_, params)] = fake.turn_params_calls
    assert params is not None
    assert params["collaborationMode"]["mode"] == "plan"
    assert any(
        "Plan cancelled; staying in plan mode" in event.text
        for event in storage.list_events("sess")
    )


@pytest.mark.asyncio
async def test_approve_codex_plan_rejects_when_not_in_plan_mode(tmp_path) -> None:
    from fastapi import HTTPException

    runtime, storage, settings = make_runtime(tmp_path)
    _codex_plugin(runtime).adapter = cast(Any, FakeCodexRuntimeAdapter())
    session = make_session(settings, permission_mode="default")
    storage.create_session(session)

    with pytest.raises(HTTPException) as exc:
        await runtime.approve_plan(
            "sess", SessionPlanApprovalRequest(plan_item_id="plan-1")
        )

    assert exc.value.status_code == 400
    assert "not in plan mode" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_approve_plan_rejects_unsupported_backend(tmp_path) -> None:
    from fastapi import HTTPException

    runtime, storage, settings = make_runtime(tmp_path)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
        permission_mode="plan",
    )
    storage.create_session(session)

    with pytest.raises(HTTPException) as exc:
        await runtime.approve_plan(
            "claude-sess", SessionPlanApprovalRequest(plan_item_id="plan-1")
        )

    assert exc.value.status_code == 400
    assert "not supported" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_approve_codex_plan_rejects_unknown_plan_item(tmp_path) -> None:
    from fastapi import HTTPException

    runtime, storage, settings = make_runtime(tmp_path)
    _codex_plugin(runtime).adapter = cast(Any, FakeCodexRuntimeAdapter())
    session = make_session(
        settings,
        permission_mode="plan",
        transport_state={"thread_id": "thread-1", "pre_plan_mode": "auto_review"},
    )
    storage.create_session(session)

    with pytest.raises(HTTPException) as exc:
        await runtime.approve_plan(
            "sess", SessionPlanApprovalRequest(plan_item_id="missing-plan")
        )

    assert exc.value.status_code == 400
    assert "plan item was not found" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_approve_codex_plan_rejects_stale_plan_item(tmp_path) -> None:
    from fastapi import HTTPException

    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        permission_mode="plan",
        transport_state={"thread_id": "thread-1", "pre_plan_mode": "auto_review"},
    )
    storage.create_session(session)
    _seed_plan_event(storage, plan_id="plan-1", text="Old plan")
    _seed_plan_event(storage, plan_id="plan-2", text="New plan")

    with pytest.raises(HTTPException) as exc:
        await runtime.approve_plan(
            "sess",
            SessionPlanApprovalRequest(plan_item_id="plan-1", decision="accept"),
        )

    assert exc.value.status_code == 400
    assert "no longer current" in str(exc.value.detail)
    assert fake.inputs == []


@pytest.mark.asyncio
async def test_approve_codex_plan_rejects_already_decided_plan_item(tmp_path) -> None:
    from fastapi import HTTPException

    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeCodexRuntimeAdapter()
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        permission_mode="plan",
        transport_state={"thread_id": "thread-1", "pre_plan_mode": "auto_review"},
    )
    storage.create_session(session)
    _seed_plan_event(storage, plan_id="plan-1", text="Plan to revise")
    storage.append_event(
        EventRecord(
            session_id="sess",
            ts=datetime.now(UTC),
            kind=EventKind.SYSTEM_NOTE,
            text="Plan declined; staying in plan mode",
            metadata={"plan_item_id": "plan-1", "plan_decision": "decline"},
            sequence=storage.next_sequence("sess"),
        )
    )

    with pytest.raises(HTTPException) as exc:
        await runtime.approve_plan(
            "sess",
            SessionPlanApprovalRequest(plan_item_id="plan-1", decision="accept"),
        )

    assert exc.value.status_code == 400
    assert "no longer current" in str(exc.value.detail)
    assert fake.inputs == []


@pytest.mark.asyncio
async def test_approve_codex_plan_send_failure_preserves_plan_state(
    tmp_path,
) -> None:
    from fastapi import HTTPException

    class FailingCodexAdapter(FakeCodexRuntimeAdapter):
        async def send_input(
            self,
            session_id: str,
            text: str,
            turn_params: dict[str, Any] | None = None,
        ) -> None:
            raise RuntimeError("send failed")

    runtime, storage, settings = make_runtime(tmp_path)
    _codex_plugin(runtime).adapter = cast(Any, FailingCodexAdapter())
    session = make_session(
        settings,
        permission_mode="plan",
        transport_state={"thread_id": "thread-1", "pre_plan_mode": "full_access"},
    )
    storage.create_session(session)
    storage.append_event(
        EventRecord(
            session_id="sess",
            ts=datetime.now(UTC),
            kind=EventKind.SYSTEM_NOTE,
            text="Completed plan",
            metadata={
                "item_id": "plan-1",
                "item_type": "plan",
                "payload": {
                    "item": {
                        "id": "plan-1",
                        "type": "plan",
                        "text": "Implement the change",
                    }
                },
            },
            sequence=storage.next_sequence("sess"),
        )
    )

    with pytest.raises(HTTPException) as exc:
        await runtime.approve_plan(
            "sess", SessionPlanApprovalRequest(plan_item_id="plan-1")
        )

    assert exc.value.status_code == 400
    refreshed = storage.get_session("sess")
    assert refreshed is not None
    assert refreshed.permission_mode == "plan"
    assert refreshed.status == SessionStatus.IDLE
    assert refreshed.transport_state["pre_plan_mode"] == "full_access"
    assert not any(
        "Plan approved; exited plan mode" in event.text
        for event in storage.list_events("sess")
    )


@pytest.mark.asyncio
async def test_set_permission_mode_codex_rejects_unknown_mode(tmp_path) -> None:
    from fastapi import HTTPException

    runtime, storage, settings = make_runtime(tmp_path)
    _codex_plugin(runtime).adapter = cast(Any, FakeStructuredAdapter())
    session = make_session(settings)
    storage.create_session(session)

    with pytest.raises(HTTPException) as exc:
        await runtime.set_permission_mode("sess", "unknown_mode")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_set_permission_mode_claude_calls_adapter(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    _claude_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
    )
    storage.create_session(session)

    updated = await runtime.set_permission_mode("claude-sess", "plan")

    assert fake.permission_mode_calls == [("claude-sess", "plan")]
    assert updated.permission_mode == "plan"


@pytest.mark.asyncio
async def test_set_permission_mode_claude_emits_invalidation_note(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    fake.pending_ids = ["approval-1"]
    _claude_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
        status=SessionStatus.WAITING_INPUT,
    )
    storage.create_session(session)

    updated = await runtime.set_permission_mode("claude-sess", "auto")

    refreshed = storage.get_session("claude-sess")
    assert refreshed is not None
    assert updated.permission_mode == "auto"
    assert refreshed.status == SessionStatus.RUNNING

    notes = [
        event
        for event in storage.list_events("claude-sess")
        if event.kind == EventKind.SYSTEM_NOTE
        and event.metadata.get("method") == "approval.invalidated"
    ]
    assert len(notes) == 1
    assert notes[0].metadata.get("approval_id") == "approval-1"
    assert "permission mode change to Auto" in notes[0].text


@pytest.mark.asyncio
async def test_approve_syncs_storage_when_adapter_flips_mode(tmp_path) -> None:
    """When ExitPlanMode is approved the Claude adapter sends
    set_permission_mode default to the binary; runtime.approve must mirror
    that into storage so the UI pill updates."""
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    fake.modes["claude-sess"] = "default"  # pretend the adapter flipped already
    _claude_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
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
async def test_approve_keeps_waiting_input_until_queue_drains(tmp_path) -> None:
    """Multi-approval queue contract: while another approval is still
    pending the session must stay WAITING_INPUT (so the pager UI keeps
    rendering) and the system_note must carry the responded approval_id
    so the frontend can dequeue precisely that card. Status flips to
    RUNNING only once the queue fully drains.
    """
    runtime, storage, settings = make_runtime(tmp_path)

    class QueueFake(FakeClaudeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.pending_ids: list[str] = ["approval-a", "approval-b"]
            self.responded: list[tuple[str, str | None, str | None]] = []

        def has_pending_approval(self, session_id: str) -> bool:
            return bool(self.pending_ids)

        async def respond_to_approval(
            self,
            session_id: str,
            decision: str,
            text: str | None = None,
            approval_id: str | None = None,
        ) -> bool:
            self.responded.append((decision, text, approval_id))
            target = approval_id or self.pending_ids[0]
            if target not in self.pending_ids:
                return False
            self.pending_ids.remove(target)
            return True

    fake = QueueFake()
    _claude_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
        status=SessionStatus.WAITING_INPUT,
    )
    storage.create_session(session)

    first = await runtime.approve(
        "claude-sess",
        SessionApprovalRequest(decision="accept", approval_id="approval-a"),
    )
    assert first.status == SessionStatus.WAITING_INPUT

    second = await runtime.approve(
        "claude-sess",
        SessionApprovalRequest(decision="accept", approval_id="approval-b"),
    )
    assert second.status == SessionStatus.RUNNING

    notes = [
        e
        for e in storage.list_events("claude-sess")
        if e.kind == EventKind.SYSTEM_NOTE and "Approval response sent" in e.text
    ]
    assert [n.metadata.get("approval_id") for n in notes] == [
        "approval-a",
        "approval-b",
    ]
    assert fake.responded == [
        ("accept", None, "approval-a"),
        ("accept", None, "approval-b"),
    ]


@pytest.mark.asyncio
async def test_set_permission_mode_claude_rejects_unknown_mode(tmp_path) -> None:
    from fastapi import HTTPException

    runtime, storage, settings = make_runtime(tmp_path)
    _claude_plugin(runtime).adapter = cast(Any, FakeClaudeAdapter())
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
    )
    storage.create_session(session)

    with pytest.raises(HTTPException) as exc:
        await runtime.set_permission_mode("claude-sess", "ultraplan")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_set_model_claude_calls_adapter_and_persists(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeClaudeAdapter()
    _claude_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend="claude_code",
        transport="claude_cli",
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
    _codex_plugin(runtime).adapter = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)

    updated = await runtime.set_model("sess", "gpt-5")

    assert fake.model_calls == [("sess", "gpt-5")]
    assert updated.model == "gpt-5"


@pytest.mark.asyncio
async def test_list_backend_models_returns_curated_claude_list(tmp_path) -> None:
    runtime, _, settings = make_runtime(tmp_path)
    response = await runtime.list_backend_models("claude_code")

    assert response["backend"] == "claude_code"
    assert response["supports_free_text"] is True
    ids = [entry["id"] for entry in response["models"]]
    # Mirrors DEFAULT_CLAUDE_MODELS in backends/claude_code/models.py.
    assert "opus" in ids and "sonnet" in ids and "haiku" in ids
    # Default falls back to the entry flagged is_default in the curated list
    # when no plugin_configs.claude_code.default_model_id override is present.
    assert response["default_model_id"] == "opus[1m]"
    assert response["default_model_label"] == "Opus 4.7 (1M context)"


@pytest.mark.asyncio
async def test_list_backend_models_honours_default_models_override(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        plugin_configs={"claude_code": {"default_model_id": "opus"}},
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    response = await runtime.list_backend_models("claude_code")
    assert response["default_model_id"] == "opus"
    assert response["default_model_label"] == "Opus 4.7"


def _seed_session_with_events(
    storage: Storage, settings: Settings, *, count: int
) -> SessionRecord:
    session = make_session(settings)
    storage.create_session(session)
    base = datetime.now(UTC)
    for index in range(count):
        storage.append_event(
            EventRecord(
                session_id=session.id,
                ts=base + timedelta(seconds=index),
                kind=EventKind.AGENT_OUTPUT,
                text=f"chunk-{index}",
                sequence=index + 1,
            )
        )
    return session


def test_session_events_page_tail_returns_latest_with_has_more_flag(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    session = _seed_session_with_events(storage, settings, count=10)

    page = runtime.session_events_page(session.id, message_limit=4)

    assert [event.sequence for event in page.events] == [7, 8, 9, 10]
    assert page.has_more is True


def test_session_events_page_before_sequence_returns_older_window(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    session = _seed_session_with_events(storage, settings, count=10)

    page = runtime.session_events_page(session.id, message_limit=3, before_sequence=7)

    assert [event.sequence for event in page.events] == [4, 5, 6]
    assert page.has_more is True


def test_session_events_page_clears_has_more_at_start_of_history(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    session = _seed_session_with_events(storage, settings, count=4)

    page = runtime.session_events_page(session.id, message_limit=10, before_sequence=3)

    assert [event.sequence for event in page.events] == [1, 2]
    # No events with sequence < 1 → caller should hide the load-more affordance.
    assert page.has_more is False


def test_session_events_page_empty_session_reports_no_more(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    session = make_session(settings)
    storage.create_session(session)

    page = runtime.session_events_page(session.id, message_limit=5)

    assert page.events == []
    assert page.has_more is False


def test_session_events_page_collapses_codex_style_delta_run(tmp_path) -> None:
    # Codex streams a single agent reply as many same-item_id deltas. The
    # paginator must treat the run as one logical message so the user
    # doesn't burn a page on a fragment of one bubble.
    runtime, storage, settings = make_runtime(tmp_path)
    session = make_session(settings)
    storage.create_session(session)
    base = datetime.now(UTC)
    storage.append_event(
        EventRecord(
            session_id=session.id,
            ts=base,
            kind=EventKind.USER_INPUT,
            text="hi",
            sequence=1,
        )
    )
    for i in range(50):
        storage.append_event(
            EventRecord(
                session_id=session.id,
                ts=base + timedelta(seconds=i + 1),
                kind=EventKind.AGENT_OUTPUT,
                text=f"d{i}",
                metadata={"item_id": "msg-A"},
                sequence=2 + i,
            )
        )

    # 1 message = the full agent reply. has_more=True because the older
    # user_input is still off-page.
    page = runtime.session_events_page(session.id, message_limit=1)
    assert len(page.events) == 50
    assert {event.kind for event in page.events} == {EventKind.AGENT_OUTPUT}
    assert page.has_more is True

    # 2 messages = user_input + the entire agent reply. has_more=False.
    page = runtime.session_events_page(session.id, message_limit=2)
    assert page.events[0].kind == EventKind.USER_INPUT
    assert page.events[0].sequence == 1
    assert len(page.events) == 51
    assert page.has_more is False
