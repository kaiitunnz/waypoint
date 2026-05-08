import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

from waypoint.backends.opencode.plugin import (
    DEFAULT_OPENCODE_MODEL,
    OpenCodePlugin,
    _ruleset_for_mode,
)
from waypoint.backends.opencode.transport import OpenCodeTransport
from waypoint.schemas import (
    CompletionDispatch,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings


def _session(**overrides: Any) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id=overrides.get("id", "sess"),
        backend="opencode",
        source=SessionSource.MANAGED,
        transport="opencode_http",
        title="Session",
        cwd=overrides.get("cwd", "/repo"),
        launch_target_id=overrides.get("launch_target_id"),
        repo_name=None,
        branch=None,
        status=overrides.get("status", SessionStatus.IDLE),
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="",
        structured_log_path="",
        transport_state={},
        permission_mode=None,
    )


def test_serialize_question_answers_preserves_choices_and_notes() -> None:
    plugin = OpenCodePlugin()

    result = plugin._serialize_question_answers(
        "fallback",
        [
            {
                "question": "Deploy target",
                "answer": "staging, prod",
                "notes": "after business hours",
            },
            {
                "question": "Rollback plan",
                "notes": "keep the old pods warm",
            },
        ],
    )

    assert result == [
        ["staging", "prod", "after business hours"],
        ["keep the old pods warm"],
    ]


def test_serialize_question_answers_falls_back_to_raw_answer() -> None:
    plugin = OpenCodePlugin()

    assert plugin._serialize_question_answers("just text", None) == [["just text"]]
    # Empty structured answers also fall back so the question still gets a reply.
    assert plugin._serialize_question_answers("just text", []) == [["just text"]]


@pytest.mark.parametrize(
    "mode,expected",
    [
        (None, None),
        ("", None),
        ("default", None),
        ("ask", [{"permission": "*", "pattern": "*", "action": "ask"}]),
        ("allow", [{"permission": "*", "pattern": "*", "action": "allow"}]),
        ("deny", [{"permission": "*", "pattern": "*", "action": "deny"}]),
    ],
)
def test_ruleset_for_mode(
    mode: str | None, expected: list[dict[str, str]] | None
) -> None:
    assert _ruleset_for_mode(mode) == expected


def test_validate_permission_mode_accepts_known_actions() -> None:
    plugin = OpenCodePlugin()

    assert plugin.validate_permission_mode(None) is None
    assert plugin.validate_permission_mode("") is None
    # "default" is a real mode (clears the ruleset) — pass it through so
    # set_permission_mode can round-trip it (the runtime rejects None).
    assert plugin.validate_permission_mode("default") == "default"
    assert plugin.validate_permission_mode("ask") == "ask"
    assert plugin.validate_permission_mode("allow") == "allow"
    assert plugin.validate_permission_mode("deny") == "deny"


def test_validate_permission_mode_rejects_legacy_auto() -> None:
    plugin = OpenCodePlugin()

    with pytest.raises(
        HTTPException, match="unsupported opencode permission mode: auto"
    ):
        plugin.validate_permission_mode("auto")


@pytest.mark.asyncio
async def test_list_command_completions_reads_opencode_commands(tmp_path) -> None:
    plugin = OpenCodePlugin()

    class FakeAdapter:
        async def list_commands(self, session_id: str) -> list[dict[str, object]]:
            assert session_id == "sess"
            return [
                {"name": "review", "description": "Review changes"},
                {"name": "skill-only", "source": "skill"},
                {"name": "compact", "description": "Duplicate static command"},
            ]

    fake_adapter = FakeAdapter()
    cast(Any, plugin)._require_adapter = lambda *args, **kwargs: fake_adapter
    runtime: Any = SimpleNamespace(
        settings=Settings(data_dir=tmp_path / "data"),
        _find_launch_target=lambda _id: None,
    )

    completions = await plugin.list_command_completions(
        runtime, _session(), trigger="/", prefix="/"
    )

    names = [item.name for item in completions]
    assert names == ["compact", "new", "review"]
    review = completions[-1]
    assert review.dispatch == CompletionDispatch.BACKEND_COMMAND
    assert review.replacement == "/review "
    assert review.description == "Review changes"


@pytest.mark.asyncio
async def test_maybe_handle_input_routes_manual_opencode_command(tmp_path) -> None:
    plugin = OpenCodePlugin()
    session = _session()

    class FakeAdapter:
        def __init__(self) -> None:
            self.executed: list[tuple[str, str, str]] = []

        async def list_commands(self, session_id: str) -> list[dict[str, object]]:
            assert session_id == "sess"
            return [{"name": "review", "description": "Review changes"}]

        async def execute_command(
            self, session_id: str, command: str, arguments: str
        ) -> None:
            self.executed.append((session_id, command, arguments))

    class FakeStorage:
        def update_session(self, session_id: str, **kwargs: object) -> SessionRecord:
            assert session_id == "sess"
            for key, value in kwargs.items():
                setattr(session, key, value)
            return session

    class FakeRuntime:
        def __init__(self) -> None:
            self.settings = Settings(data_dir=tmp_path / "data")
            self.storage = FakeStorage()
            self.user_events: list[tuple[str, str, bool]] = []

        def _find_launch_target(self, launch_target_id: str | None) -> None:
            return None

        async def _record_user_event(
            self, session_id: str, text: str, submit: bool = True, **kwargs: object
        ) -> None:
            self.user_events.append((session_id, text, submit))

    fake_adapter = FakeAdapter()
    cast(Any, plugin)._require_adapter = lambda *args, **kwargs: fake_adapter
    runtime: Any = FakeRuntime()

    result = await plugin.maybe_handle_input(
        runtime, session, SessionInputRequest(text="/review auth flow")
    )

    assert result is session
    assert result.status == SessionStatus.RUNNING
    assert runtime.user_events == [("sess", "/review auth flow", True)]
    assert fake_adapter.executed == [("sess", "review", "auth flow")]


def test_flatten_provider_models_skips_invalid_and_deprecated() -> None:
    plugin = OpenCodePlugin()
    payload = {
        "all": [
            {
                "id": "opencode",
                "name": "OpenCode",
                "models": {
                    "minimax-m2.5-free": {
                        "name": "MiniMax M2.5 Free",
                        "status": "active",
                    },
                    "old-model": {"name": "Old", "status": "deprecated"},
                },
            },
            {
                "id": "anthropic",
                "name": "Anthropic",
                "models": {
                    "claude-sonnet-4-6": {"status": "active"},
                },
            },
            "not-a-dict",
            {"id": ""},
        ],
    }

    flattened = plugin._flatten_provider_models(payload, include_hidden=False)

    assert flattened == [
        {
            "id": "anthropic/claude-sonnet-4-6",
            "label": "Anthropic · claude-sonnet-4-6",
            "supported_efforts": [],
            "default_effort": None,
        },
        {
            "id": "opencode/minimax-m2.5-free",
            "label": "OpenCode · MiniMax M2.5 Free",
            "supported_efforts": [],
            "default_effort": None,
        },
    ]


def test_flatten_provider_models_filters_unconnected_providers() -> None:
    plugin = OpenCodePlugin()
    payload = {
        "connected": ["opencode"],
        "all": [
            {
                "id": "opencode",
                "name": "OpenCode",
                "models": {
                    "minimax-m2.5-free": {"name": "MiniMax", "status": "active"}
                },
            },
            {
                "id": "openrouter",
                "name": "OpenRouter",
                "models": {
                    "minimax/minimax-m2.5:free": {
                        "name": "MiniMax Free",
                        "status": "active",
                    },
                },
            },
        ],
    }

    flattened = plugin._flatten_provider_models(payload, include_hidden=False)

    # Only the connected provider's models are surfaced — listing OpenRouter
    # without an API key would let the user pick a model that the runtime
    # rejects with "Model not found" once a prompt is sent.
    assert flattened == [
        {
            "id": "opencode/minimax-m2.5-free",
            "label": "OpenCode · MiniMax",
            "supported_efforts": [],
            "default_effort": None,
        },
    ]


def test_flatten_provider_models_includes_deprecated_when_requested() -> None:
    plugin = OpenCodePlugin()
    payload = {
        "all": [
            {
                "id": "opencode",
                "name": "OpenCode",
                "models": {
                    "old-model": {"name": "Old", "status": "deprecated"},
                },
            },
        ],
    }

    flattened = plugin._flatten_provider_models(payload, include_hidden=True)

    assert flattened == [
        {
            "id": "opencode/old-model",
            "label": "OpenCode · Old",
            "supported_efforts": [],
            "default_effort": None,
        }
    ]


def test_select_default_model_prefers_user_default_when_available() -> None:
    plugin = OpenCodePlugin()
    models = [
        {"id": DEFAULT_OPENCODE_MODEL, "label": "MiniMax"},
        {"id": "anthropic/claude-sonnet-4-6", "label": "Sonnet"},
    ]

    assert plugin._select_default_model(models, providers={}) == (
        DEFAULT_OPENCODE_MODEL,
        "MiniMax",
    )


def test_select_default_model_falls_back_to_provider_defaults() -> None:
    plugin = OpenCodePlugin()
    models = [{"id": "anthropic/claude-sonnet-4-6", "label": "Sonnet"}]
    providers = {"default": {"anthropic": "claude-sonnet-4-6", "missing": "x"}}

    assert plugin._select_default_model(models, providers) == (
        "anthropic/claude-sonnet-4-6",
        "Sonnet",
    )


def test_select_default_model_falls_back_to_first_available() -> None:
    plugin = OpenCodePlugin()
    models = [{"id": "anthropic/claude-sonnet-4-6", "label": "Sonnet"}]

    assert plugin._select_default_model(models, providers={}) == (
        "anthropic/claude-sonnet-4-6",
        "Sonnet",
    )


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[object, ...]]] = []

    async def send_input(self, session_id: str, text: str) -> None:
        self.calls.append(("send_input", session_id, (text,)))

    async def interrupt(self, session_id: str) -> None:
        self.calls.append(("interrupt", session_id, ()))

    async def terminate_session(self, session_id: str) -> bool:
        self.calls.append(("terminate_session", session_id, ()))
        return True

    async def respond_to_permission(
        self,
        session_id: str,
        decision: str,
        text: str | None = None,
        approval_id: str | None = None,
    ) -> bool:
        self.calls.append(("respond_to_permission", session_id, (decision,)))
        return True

    def has_pending_approval(self, session_id: str) -> bool:
        self.calls.append(("has_pending_approval", session_id, ()))
        return True

    def terminal_snapshot(self, session_id: str) -> str:
        self.calls.append(("terminal_snapshot", session_id, ()))
        return "snapshot"


@pytest.mark.asyncio
async def test_transport_routes_calls_by_session_launch_target() -> None:
    plugin = OpenCodePlugin()
    default_adapter = _FakeAdapter()
    remote_adapter = _FakeAdapter()
    plugin._adapters = cast(
        Any,
        {
            (None, "/tmp", ()): default_adapter,
            ("ssh-1", "/tmp", ()): remote_adapter,
        },
    )

    fake_runtime = cast(
        Any,
        SimpleNamespace(
            _find_launch_target=lambda _id: None,
            settings=SimpleNamespace(
                plugin_config=lambda _id: SimpleNamespace(cli_args=[])
            ),
        ),
    )
    transport = OpenCodeTransport(runtime=fake_runtime, plugin=plugin)
    remote_session = SessionRecord(
        id="sess-remote",
        backend="opencode",
        source=SessionSource.MANAGED,
        transport=plugin.transport_id,
        title="Remote",
        cwd="/tmp",
        launch_target_id="ssh-1",
        repo_name=None,
        branch=None,
        status=SessionStatus.IDLE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        last_event_at=datetime.now(UTC),
        raw_log_path="",
        structured_log_path="",
        transport_state={},
        permission_mode=None,
    )

    await transport.send_input(remote_session, "hello")
    assert remote_adapter.calls == [("send_input", "sess-remote", ("hello",))]
    assert default_adapter.calls == []

    await transport.interrupt(remote_session)
    await transport.terminate(remote_session)
    assert remote_adapter.calls[1:] == [
        ("interrupt", "sess-remote", ()),
        ("terminate_session", "sess-remote", ()),
    ]

    handled = await transport.respond_to_approval(remote_session, "approve", None)
    assert handled is True
    assert remote_adapter.calls[-1] == (
        "respond_to_permission",
        "sess-remote",
        ("approve",),
    )
    assert transport.has_pending_approval(remote_session) is True
    assert transport.terminal_snapshot(remote_session) == "snapshot"


@pytest.mark.asyncio
async def test_get_or_create_adapter_keys_by_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = OpenCodePlugin()

    class FakeAdapter:
        def __init__(
            self,
            emit_event,
            launch_target=None,
            on_agent_changed=None,
            on_server_died=None,
            workdir=None,
            extra_args=(),
        ) -> None:
            self.workdir = workdir

    runtime: Any = SimpleNamespace(
        settings=SimpleNamespace(default_cwd="~/"),
        _find_launch_target=lambda _launch_target_id: SimpleNamespace(
            default_cwd="~/project"
        ),
        _emit_adapter_event=lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        "waypoint.backends.opencode.plugin.OpenCodeAdapter", FakeAdapter
    )
    adapter_one = cast(
        Any, await plugin._get_or_create_adapter(runtime, "ssh-1", "/tmp/a")
    )
    adapter_two = cast(
        Any, await plugin._get_or_create_adapter(runtime, "ssh-1", "/tmp/a")
    )
    adapter_three = cast(
        Any, await plugin._get_or_create_adapter(runtime, "ssh-1", "/tmp/b")
    )

    assert adapter_one is adapter_two
    assert adapter_one is not adapter_three
    assert adapter_one.workdir == "/tmp/a"
    assert adapter_three.workdir == "/tmp/b"


@pytest.mark.asyncio
async def test_get_or_create_adapter_normalizes_equivalent_cwds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = OpenCodePlugin()

    class FakeAdapter:
        def __init__(
            self,
            emit_event,
            launch_target=None,
            on_agent_changed=None,
            on_server_died=None,
            workdir=None,
            extra_args=(),
        ) -> None:
            self.workdir = workdir

    runtime: Any = SimpleNamespace(
        settings=SimpleNamespace(default_cwd="~/"),
        _find_launch_target=lambda _launch_target_id: None,
        _emit_adapter_event=lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        "waypoint.backends.opencode.plugin.OpenCodeAdapter", FakeAdapter
    )
    adapter_one = cast(
        Any, await plugin._get_or_create_adapter(runtime, None, "/tmp/a/")
    )
    adapter_two = cast(
        Any, await plugin._get_or_create_adapter(runtime, None, "/tmp/a")
    )

    assert adapter_one is adapter_two
    assert adapter_one.workdir == "/tmp/a"


@pytest.mark.asyncio
async def test_list_models_uses_target_adapter_independent_of_cwd() -> None:
    plugin = OpenCodePlugin()

    class FakeAdapter:
        def __init__(self, providers: dict[str, object]) -> None:
            self._providers = providers
            self.called = False

        async def list_providers(self) -> dict[str, object]:
            self.called = True
            return self._providers

    stale_adapter = FakeAdapter({"all": [], "default": {}, "connected": []})
    healthy_adapter = FakeAdapter(
        {
            "all": [
                {
                    "id": "opencode",
                    "name": "OpenCode",
                    "models": {
                        "minimax-m2.5-free": {
                            "name": "MiniMax",
                            "status": "active",
                        }
                    },
                }
            ],
            "default": {"opencode": "minimax-m2.5-free"},
            "connected": ["opencode"],
        }
    )
    plugin._adapters = cast(
        Any,
        {
            ("ssh-1", "/repo-a"): stale_adapter,
            ("ssh-1", "/repo-b"): healthy_adapter,
        },
    )

    async def fail_get_or_create_adapter(*args, **kwargs):
        raise AssertionError("list_models should reuse an existing target adapter")

    plugin._get_or_create_adapter = fail_get_or_create_adapter  # type: ignore[method-assign]

    runtime: Any = SimpleNamespace(storage=SimpleNamespace())

    result = await plugin.list_models(
        runtime,
        launch_target_id="ssh-1",
    )

    assert healthy_adapter.called is True
    assert stale_adapter.called is False
    assert result["default_model_id"] == "opencode/minimax-m2.5-free"


@pytest.mark.asyncio
async def test_list_threads_ignores_cwd_and_dedupes_by_launch_target() -> None:
    plugin = OpenCodePlugin()

    class FakeAdapter:
        def __init__(self, sessions: list[dict[str, object]]) -> None:
            self._sessions = sessions

        async def list_sessions(self) -> list[dict[str, object]]:
            return list(self._sessions)

    plugin._adapters = cast(
        Any,
        {
            ("ssh-1", "/repo-a"): FakeAdapter(
                [
                    {
                        "id": "ses_1",
                        "title": "Imported candidate",
                        "directory": "/repo-a",
                        "time": {"created": 11, "updated": 22},
                    }
                ]
            ),
            ("ssh-1", "/repo-b"): FakeAdapter(
                [
                    {
                        "id": "ses_1",
                        "title": "Duplicate from another worktree",
                        "directory": "/repo-b",
                        "time": {"created": 33, "updated": 44},
                    },
                    {
                        "id": "ses_2",
                        "title": "Fresh candidate",
                        "directory": "/repo-b",
                        "time": {"created": 55, "updated": 66},
                    },
                ]
            ),
        },
    )

    async def fail_get_or_create_adapter(*args, **kwargs):
        raise AssertionError("list_threads should reuse an existing target adapter")

    plugin._get_or_create_adapter = fail_get_or_create_adapter  # type: ignore[method-assign]

    imported = SessionRecord(
        id="waypoint-1",
        backend="opencode",
        source=SessionSource.MANAGED,
        transport=plugin.transport_id,
        title="Imported",
        cwd="/repo-b",
        launch_target_id="ssh-1",
        repo_name=None,
        branch=None,
        status=SessionStatus.IDLE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        last_event_at=datetime.now(UTC),
        raw_log_path="",
        structured_log_path="",
        transport_state={"opencode_session_id": "ses_1"},
        permission_mode=None,
    )

    runtime: Any = SimpleNamespace(
        storage=SimpleNamespace(list_sessions=lambda: [imported]),
    )

    threads = await plugin.list_threads(runtime, launch_target_id="ssh-1")

    assert [thread.model_dump(mode="json") for thread in threads] == [
        {
            "id": "ses_2",
            "title": "Fresh candidate",
            "directory": "/repo-b",
            "created_at": 55,
            "updated_at": 66,
        }
    ]


@pytest.mark.asyncio
async def test_list_threads_sorts_by_updated_at_after_merging_adapters() -> None:
    plugin = OpenCodePlugin()

    class FakeAdapter:
        def __init__(self, sessions: list[dict[str, object]]) -> None:
            self._sessions = sessions

        async def list_sessions(self) -> list[dict[str, object]]:
            return list(self._sessions)

    plugin._adapters = cast(
        Any,
        {
            ("ssh-1", "/repo-a"): FakeAdapter(
                [
                    {
                        "id": "ses_a",
                        "title": "Older candidate",
                        "directory": "/repo-a",
                        "time": {"created": 11, "updated": 22},
                    }
                ]
            ),
            ("ssh-1", "/repo-b"): FakeAdapter(
                [
                    {
                        "id": "ses_b",
                        "title": "Newer candidate",
                        "directory": "/repo-b",
                        "time": {"created": 33, "updated": 44},
                    }
                ]
            ),
        },
    )

    runtime: Any = SimpleNamespace(storage=SimpleNamespace(list_sessions=lambda: []))

    threads = await plugin.list_threads(runtime, launch_target_id="ssh-1")

    assert [thread.id for thread in threads] == ["ses_b", "ses_a"]


@pytest.mark.asyncio
async def test_import_thread_preserves_launch_target_id() -> None:
    plugin = OpenCodePlugin()

    class FakeAdapter:
        async def get_session(self, session_id: str) -> dict[str, object] | None:
            return {
                "id": session_id,
                "title": "Imported",
                "directory": "/repo",
            }

        async def restore_session(
            self,
            session_id: str,
            cwd: str,
            opencode_session_id: str,
            model: str | None = None,
            agent: str | None = None,
            effort: str | None = None,
        ) -> None:
            return None

    fake_adapter = FakeAdapter()

    async def fake_get_or_create_adapter(
        runtime, launch_target_id, cwd, custom_args=(), *, user_initiated=False
    ):
        assert launch_target_id == "ssh-1"
        assert cwd == "/repo"
        return fake_adapter

    plugin._get_or_create_adapter = fake_get_or_create_adapter  # type: ignore[method-assign]

    class FakeStorage:
        def __init__(self) -> None:
            self.sessions: list[SessionRecord] = []

        def list_sessions(self) -> list[SessionRecord]:
            return list(self.sessions)

        def create_session(self, session: SessionRecord) -> None:
            self.sessions.append(session)

        def update_session(self, session_id: str, **kwargs) -> SessionRecord:
            session = self.sessions[-1]
            for key, value in kwargs.items():
                setattr(session, key, value)
            return session

    class FakeRuntime:
        def __init__(self) -> None:
            self.storage = FakeStorage()

        def _generate_session_id(self, backend_id: str) -> str:
            return f"{backend_id}-1"

        def _session_dir(self, session_id: str):
            from pathlib import Path

            path = Path("/tmp") / session_id
            path.mkdir(parents=True, exist_ok=True)
            return path

        async def _record_system_event(self, *args, **kwargs) -> None:
            return None

        def get_session(self, session_id: str) -> SessionRecord:
            return self.storage.sessions[-1]

    runtime: Any = FakeRuntime()
    request = type(
        "Req",
        (),
        {"thread_id": "ses_1", "launch_target_id": "ssh-1", "cwd": "/repo"},
    )()

    result = await plugin.import_thread(runtime, request)

    assert result.launch_target_id == "ssh-1"


@pytest.mark.asyncio
async def test_import_thread_keys_adapter_by_session_directory() -> None:
    # When the user-supplied requested_cwd doesn't match the OpenCode
    # session's actual directory, the adapter must be cached under the
    # session's directory so subsequent _require_adapter(..., session.cwd)
    # lookups find it.
    plugin = OpenCodePlugin()

    class FakeAdapter:
        async def get_session(self, session_id: str) -> dict[str, object] | None:
            return {
                "id": session_id,
                "title": "Imported",
                "directory": "/repo/actual",
            }

        async def restore_session(
            self,
            session_id: str,
            cwd: str,
            opencode_session_id: str,
            model: str | None = None,
            agent: str | None = None,
            effort: str | None = None,
        ) -> None:
            return None

    fake_adapter = FakeAdapter()
    cwds_seen: list[str | None] = []

    async def fake_get_or_create_adapter(
        runtime, launch_target_id, cwd, custom_args=(), *, user_initiated=False
    ):
        cwds_seen.append(cwd)
        return fake_adapter

    plugin._get_or_create_adapter = fake_get_or_create_adapter  # type: ignore[method-assign]

    class FakeStorage:
        def __init__(self) -> None:
            self.sessions: list[SessionRecord] = []

        def list_sessions(self) -> list[SessionRecord]:
            return list(self.sessions)

        def create_session(self, session: SessionRecord) -> None:
            self.sessions.append(session)

        def update_session(self, session_id: str, **kwargs) -> SessionRecord:
            session = self.sessions[-1]
            for key, value in kwargs.items():
                setattr(session, key, value)
            return session

    class FakeRuntime:
        def __init__(self) -> None:
            self.storage = FakeStorage()

        def _generate_session_id(self, backend_id: str) -> str:
            return f"{backend_id}-1"

        def _session_dir(self, session_id: str):
            from pathlib import Path

            path = Path("/tmp") / session_id
            path.mkdir(parents=True, exist_ok=True)
            return path

        async def _record_system_event(self, *args, **kwargs) -> None:
            return None

        def get_session(self, session_id: str) -> SessionRecord:
            return self.storage.sessions[-1]

    runtime: Any = FakeRuntime()
    request = type(
        "Req",
        (),
        {
            "thread_id": "ses_1",
            "launch_target_id": "ssh-1",
            "cwd": "/repo/requested",
        },
    )()

    result = await plugin.import_thread(runtime, request)

    # The persisted SessionRecord uses the session's actual directory,
    # not the requested cwd.
    assert result.cwd == "/repo/actual"
    # The final adapter lookup must be keyed by /repo/actual so future
    # _require_adapter calls (which key by session.cwd) hit a live adapter.
    assert "/repo/actual" in cwds_seen


@pytest.mark.asyncio
async def test_shutdown_drains_pending_tasks() -> None:
    plugin = OpenCodePlugin()

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _slow_callback() -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(_slow_callback())
    plugin._pending_tasks.add(task)
    task.add_done_callback(plugin._pending_tasks.discard)

    await started.wait()

    runtime: Any = SimpleNamespace()
    await plugin.shutdown(runtime)

    assert cancelled.is_set()
    assert plugin._pending_tasks == set()
    assert plugin._shutting_down is True


def test_resume_slash_command_removed() -> None:
    from waypoint.backends.opencode.plugin import OPENCODE_SLASH_COMMANDS

    names = {cmd.name for cmd in OPENCODE_SLASH_COMMANDS}
    assert "resume" not in names
    assert "compact" in names
