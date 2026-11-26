import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from waypoint.attachments import ResolvedAttachment
from waypoint.backends.opencode.adapter import OpenCodeError
from waypoint.backends.opencode.catalog import Model
from waypoint.backends.opencode.plugin import (
    OPENCODE_SLASH_COMMANDS,
    OpenCodePlugin,
    OpenCodePluginConfig,
    OpenCodeThreadImportRequest,
    _ruleset_for_mode,
)
from waypoint.backends.opencode.transport import OpenCodeTransport
from waypoint.git_meta import GitMeta
from waypoint.questions import QuestionDecline
from waypoint.schemas import (
    AttachmentSpec,
    CommandCompletion,
    CompletionDispatch,
    EventKind,
    EventRecord,
    SessionCommandInvocation,
    SessionCreateRequest,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)

ASK = [{"action": "*", "resource": "*", "effect": "ask"}]


def _session(**overrides: Any) -> SessionRecord:
    now = datetime.now(UTC)
    fields: dict[str, Any] = {
        "id": "sess",
        "backend": "opencode",
        "source": SessionSource.MANAGED,
        "transport": "opencode_http",
        "title": "Session",
        "cwd": "/repo",
        "launch_target_id": None,
        "repo_name": None,
        "branch": None,
        "status": SessionStatus.IDLE,
        "created_at": now,
        "updated_at": now,
        "last_event_at": now,
        "raw_log_path": "",
        "structured_log_path": "",
        "transport_state": {},
    }
    return SessionRecord(**{**fields, **overrides})


def _passthrough_agent_process_env(
    backend: str,
    launch_env: dict[str, str],
    *,
    session_id: str | None = None,
) -> dict[str, str]:
    return dict(launch_env)


class _Storage:
    def __init__(self, *sessions: SessionRecord) -> None:
        self.sessions = {session.id: session for session in sessions}
        self.approval_events: list[EventRecord] = []
        self.open_questions: list[str] = []

    def get_session(self, session_id: str) -> SessionRecord | None:
        return self.sessions.get(session_id)

    def list_sessions(self) -> list[SessionRecord]:
        return list(self.sessions.values())

    def create_session(self, session: SessionRecord) -> None:
        self.sessions[session.id] = session

    def update_session(self, session_id: str, **fields: Any) -> SessionRecord:
        session = self.sessions[session_id]
        for key, value in fields.items():
            setattr(session, key, value)
        return session

    def restore_status(self, session: SessionRecord) -> None:
        self.update_session(session.id, status=session.status)

    def clone_events(self, _source_id: str, _target_id: str) -> None:
        return None

    def list_approval_events(self, _session_id: str) -> list[EventRecord]:
        return self.approval_events

    def open_question_tool_use_ids(self, _session_id: str) -> list[str]:
        return self.open_questions


def _runtime(*sessions: SessionRecord, **extra: Any) -> Any:
    storage = _Storage(*sessions)
    runtime = SimpleNamespace(
        storage=storage,
        settings=SimpleNamespace(
            default_cwd="~/", plugin_config=lambda _id: OpenCodePluginConfig()
        ),
        _find_launch_target=lambda _id: None,
        _agent_process_env=_passthrough_agent_process_env,
        _record_system_event=AsyncMock(),
        _record_user_event=AsyncMock(),
        _emit_adapter_event=AsyncMock(),
        get_session=lambda session_id: storage.sessions[session_id],
        broadcast_session_list=AsyncMock(),
        questions=MagicMock(reconcile=AsyncMock()),
        asks=MagicMock(),
    )
    for key, value in extra.items():
        setattr(runtime, key, value)
    return runtime


def _live(
    plugin: OpenCodePlugin, runtime: Any, session: SessionRecord, adapter: Any
) -> None:
    plugin._adapters[plugin._session_adapter_key(runtime, session)] = adapter


@pytest.mark.parametrize(
    "mode,expected",
    [
        (None, []),
        ("default", []),
        ("plan", []),
        ("ask", ASK),
        ("allow", [{"action": "*", "resource": "*", "effect": "allow"}]),
        ("deny", [{"action": "*", "resource": "*", "effect": "deny"}]),
    ],
)
def test_ruleset_for_mode(mode: str | None, expected: list[dict[str, str]]) -> None:
    assert _ruleset_for_mode(mode) == expected


def test_validate_permission_mode_accepts_known_actions() -> None:
    plugin = OpenCodePlugin()

    assert plugin.validate_permission_mode(None) is None
    assert plugin.validate_permission_mode("") is None
    # "default" is a real mode (clears the ruleset) — pass it through so
    # set_permission_mode can round-trip it (the runtime rejects None).
    for mode in ("default", "ask", "allow", "deny", "plan"):
        assert plugin.validate_permission_mode(mode) == mode
    with pytest.raises(
        HTTPException, match="unsupported opencode permission mode: auto"
    ):
        plugin.validate_permission_mode("auto")


@pytest.mark.parametrize(
    ("mode", "agent", "rules"), [("plan", "plan", []), ("ask", None, ASK)]
)
@pytest.mark.asyncio
async def test_create_session_passes_the_modes_agent_and_rules(
    tmp_path: Path, mode: str, agent: str | None, rules: list[dict[str, str]]
) -> None:
    plugin = OpenCodePlugin()
    adapter = MagicMock(
        start_session=AsyncMock(return_value="ses_new"),
        selected_model=MagicMock(return_value=(None, None)),
    )
    cast(Any, plugin)._get_or_create_adapter = AsyncMock(return_value=adapter)
    runtime = _runtime()

    session = await plugin.create_session(
        runtime,
        SessionCreateRequest(backend="opencode", cwd="/repo", permission_mode=mode),
        session_id="sess",
        launch_target=None,
        title="T",
        raw_log=tmp_path / "raw.log",
        structured_log=tmp_path / "events.jsonl",
        git_meta=GitMeta(repo_name="repo", branch="main"),
        permission_mode=mode,
        resolved_model=None,
        resolved_effort=None,
    )

    kwargs = adapter.start_session.await_args.kwargs
    assert (kwargs["agent"], kwargs["permissions"]) == (agent, rules)
    expected = {"opencode_session_id": "ses_new"}
    if agent:
        expected |= {"agent": "plan", "pre_plan_mode": "default"}
    assert session.transport_state == expected


# ── Commands and input ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_completions_list_commands_and_skills() -> None:
    plugin = OpenCodePlugin()
    session = _session()
    runtime = _runtime(session)
    adapter = MagicMock(
        list_commands=AsyncMock(
            return_value=[
                {"name": "review", "description": "Review", "source": "command"},
                {"name": "compact", "description": "Duplicate", "source": "command"},
                {"name": "wp-skill", "description": "Skill", "source": "skill"},
                {"name": "review", "description": "Same name", "source": "skill"},
            ]
        )
    )
    _live(plugin, runtime, session, adapter)

    completions = await plugin.list_command_completions(runtime, session)

    assert [item.name for item in completions] == [
        "compact",
        "new",
        "status",
        "review",
        "wp-skill",
    ]
    review, skill = completions[-2:]
    assert review.dispatch == CompletionDispatch.BACKEND_COMMAND
    assert (review.replacement, review.kind) == ("/review ", "command")
    assert (skill.kind, skill.source) == ("skill", "opencode_skill")
    assert skill.metadata == {"source": "skill"}


@pytest.mark.asyncio
async def test_completions_without_a_server_are_the_static_ones() -> None:
    plugin = OpenCodePlugin()

    completions = await plugin.list_command_completions(_runtime(), _session())

    assert [item.name for item in completions] == ["compact", "new", "status"]


def _completion(name: str, source: str) -> CommandCompletion:
    return CommandCompletion(
        id=f"opencode:{source}:{name}",
        trigger="/",
        replacement=f"/{name} ",
        name=name,
        kind=source,
        source=f"opencode_{source}",
        dispatch=CompletionDispatch.BACKEND_COMMAND,
        metadata={"source": source},
    )


@pytest.mark.parametrize("source", ["command", "skill"])
@pytest.mark.parametrize("picked", [True, False])
@pytest.mark.asyncio
async def test_a_command_or_skill_records_and_sends_its_attachments(
    tmp_path: Path, source: str, picked: bool
) -> None:
    plugin = OpenCodePlugin()
    session = _session()
    spec = AttachmentSpec(
        id="att_1", filename="a.txt", mime="text/plain", size=1, kind="file"
    )
    resolved = [ResolvedAttachment(spec=spec, path=tmp_path / "a.txt")]
    marked: list[Any] = []
    runtime = _runtime(
        session,
        resolve_attachments=lambda _sid, ids: resolved if ids else [],
        attachments=SimpleNamespace(
            mark_sent=lambda sid, ids: marked.append((sid, ids))
        ),
        cached_command_completion=lambda _sid, trigger, name: _completion(name, source),
    )
    adapter = MagicMock(send_input=AsyncMock(), execute_command=AsyncMock())
    _live(plugin, runtime, session, adapter)
    invocation = SessionCommandInvocation(
        completion_id="c",
        name="wp",
        arguments="auth flow",
        dispatch=CompletionDispatch.BACKEND_COMMAND,
        metadata={"source": source},
    )

    result = await plugin.maybe_handle_input(
        runtime,
        session,
        SessionInputRequest(
            text="/wp auth flow",
            attachments=["att_1"],
            command=invocation if picked else None,
        ),
    )

    assert result is session and session.status == SessionStatus.RUNNING
    assert runtime._record_user_event.await_args.kwargs["attachments"] == [spec]
    assert marked == [("sess", ["att_1"])]
    if source == "skill":
        adapter.send_input.assert_awaited_once_with(
            "sess", "/wp auth flow", resolved, skills=["wp"]
        )
        adapter.execute_command.assert_not_awaited()
    else:
        adapter.execute_command.assert_awaited_once_with(
            "sess", "wp", "auth flow", resolved
        )
        adapter.send_input.assert_not_awaited()


@pytest.mark.parametrize("text", ["/compact", "/review x"])
@pytest.mark.asyncio
async def test_a_refused_command_restores_the_status(text: str) -> None:
    plugin = OpenCodePlugin()
    session = _session()
    runtime = _runtime(
        session,
        resolve_attachments=lambda _sid, _ids: [],
        cached_command_completion=lambda _sid, trigger, name: _completion(
            name, "command"
        ),
    )
    refused = AsyncMock(side_effect=OpenCodeError("nope"))
    adapter = MagicMock(compact_session=refused, execute_command=refused)
    _live(plugin, runtime, session, adapter)

    async def compacting(*_args: Any, status: SessionStatus, **_kwargs: Any) -> None:
        runtime.storage.update_session("sess", status=status)

    runtime._record_system_event = compacting
    before = session.model_copy()

    with pytest.raises(HTTPException, match="nope"):
        await plugin.maybe_handle_input(runtime, before, SessionInputRequest(text=text))

    assert runtime.storage.sessions["sess"].status == SessionStatus.IDLE


# ── Models ──────────────────────────────────────────────────────────────


class _ModelAdapter:
    def __init__(self, models: list[Model]) -> None:
        self.models = models
        self.called = False

    async def list_models(self, *, include_deprecated: bool = False) -> list[Model]:
        self.called = True
        return self.models

    async def provider_names(self) -> dict[str, str]:
        return {"opencode": "OpenCode Zen"}

    async def default_model_id(self) -> str:
        return "opencode/big-pickle"


@pytest.mark.asyncio
async def test_list_models_reads_a_target_adapter_with_models() -> None:
    plugin = OpenCodePlugin()
    empty = _ModelAdapter([])
    healthy = _ModelAdapter(
        [
            Model(
                "opencode",
                "big-pickle",
                "Big Pickle",
                variants=("max", "high", "low", "turbo"),
            ),
            Model("acme", "m1", "M1"),
        ]
    )
    plugin._adapters = cast(
        Any,
        {
            ("ssh-1", "/repo-a", (), ()): healthy,
            ("ssh-1", "/repo-b", (), ()): empty,
        },
    )
    cast(Any, plugin)._get_or_create_adapter = AsyncMock(
        side_effect=AssertionError("reuses an existing target adapter")
    )

    result = await plugin.list_models(_runtime(), launch_target_id="ssh-1")

    assert empty.called and healthy.called
    assert result["default_model_id"] == "opencode/big-pickle"
    assert result["default_model_label"] == "OpenCode Zen · Big Pickle"
    assert [(m["id"], m["label"]) for m in result["models"]] == [
        ("acme/m1", "acme · M1"),
        ("opencode/big-pickle", "OpenCode Zen · Big Pickle"),
    ]
    assert result["models"][1]["supported_efforts"] == ["low", "high", "max", "turbo"]


@pytest.mark.parametrize("method", ["apply_model", "apply_effort"])
@pytest.mark.asyncio
async def test_a_selection_that_fails_is_refused(method: str) -> None:
    plugin = OpenCodePlugin()
    session = _session()
    runtime = _runtime(session)
    refused = AsyncMock(side_effect=OpenCodeError("no such model"))
    _live(plugin, runtime, session, MagicMock(set_model=refused, set_effort=refused))

    with pytest.raises(HTTPException, match="no such model"):
        await getattr(plugin, method)(runtime, session, "x/y")


@pytest.mark.asyncio
async def test_clearing_the_model_reaches_the_server() -> None:
    plugin = OpenCodePlugin()
    session = _session(effort="high")
    runtime = _runtime(session)
    adapter = MagicMock(set_model=AsyncMock(return_value=True))
    _live(plugin, runtime, session, adapter)

    await plugin.apply_model(runtime, session, None)

    adapter.set_model.assert_awaited_once_with("sess", None, "high")


# ── Permission modes and plan ───────────────────────────────────────────


def _mode_adapter(agent: str | None) -> MagicMock:
    return MagicMock(
        set_agent=AsyncMock(return_value=True),
        set_pre_plan_mode=MagicMock(),
        set_session_permission=AsyncMock(return_value=True),
        get_agent=MagicMock(return_value=agent),
    )


@pytest.mark.parametrize(
    ("asked", "selected", "noted"),
    [("max", None, True), ("high", "high", False), ("default", None, False)],
)
@pytest.mark.asyncio
async def test_a_launch_effort_the_model_lacks_is_not_recorded(
    tmp_path: Path, asked: str, selected: str | None, noted: bool
) -> None:
    plugin = OpenCodePlugin()
    adapter = MagicMock(
        start_session=AsyncMock(return_value="ses_new"),
        selected_model=MagicMock(return_value=("opencode/big-pickle", selected)),
    )
    cast(Any, plugin)._get_or_create_adapter = AsyncMock(return_value=adapter)
    runtime = _runtime()

    session = await plugin.create_session(
        runtime,
        SessionCreateRequest(backend="opencode", cwd="/repo"),
        session_id="sess",
        launch_target=None,
        title="T",
        raw_log=tmp_path / "raw.log",
        structured_log=tmp_path / "events.jsonl",
        git_meta=GitMeta(repo_name="repo", branch="main"),
        permission_mode="default",
        resolved_model="opencode/big-pickle",
        resolved_effort=asked,
    )

    assert session.effort == (None if noted else asked)
    assert runtime._record_system_event.await_count == int(noted)


@pytest.mark.asyncio
async def test_thread_discovery_and_import_say_why_the_server_is_unusable() -> None:
    plugin = OpenCodePlugin()
    refused = OpenCodeError("OpenCode 1.x is not supported; upgrade OpenCode to 2.x")
    adapter = MagicMock(
        list_sessions=AsyncMock(side_effect=refused),
        get_session=AsyncMock(side_effect=refused),
        workdir="/repo",
    )
    cast(Any, plugin)._get_or_create_adapter = AsyncMock(return_value=adapter)
    cast(Any, plugin)._adapters_for_launch_target = MagicMock(return_value=[adapter])
    cast(Any, plugin)._find_adapter_for_launch_target = MagicMock(return_value=adapter)
    runtime = _runtime()

    with pytest.raises(HTTPException, match="upgrade OpenCode") as listing:
        await plugin.list_threads(runtime)
    assert listing.value.status_code == 503

    with pytest.raises(HTTPException, match="upgrade OpenCode") as importing:
        await plugin.import_thread(
            runtime, OpenCodeThreadImportRequest(thread_id="ses_1")
        )
    assert importing.value.status_code == 400


@pytest.mark.parametrize(
    ("current", "mode", "agent_call", "rules", "subagent_rules"),
    [
        # A running subagent does not take the plan agent, so it asks.
        (None, "plan", "plan", [], ASK),
        ("plan", "ask", "build", ASK, ASK),
        ("build", "ask", None, ASK, ASK),
        (None, "default", None, [], []),
    ],
)
@pytest.mark.asyncio
async def test_a_mode_change_applies_the_agent_and_rules_at_once(
    current: str | None,
    mode: str,
    agent_call: str | None,
    rules: list[Any],
    subagent_rules: list[Any],
) -> None:
    plugin = OpenCodePlugin()
    session = _session(permission_mode="allow")
    runtime = _runtime(session)
    adapter = _mode_adapter(current)
    _live(plugin, runtime, session, adapter)

    await plugin.apply_permission_mode(runtime, session, mode)

    if agent_call:
        adapter.set_agent.assert_awaited_once_with("sess", agent_call)
    else:
        adapter.set_agent.assert_not_awaited()
    adapter.set_session_permission.assert_awaited_once_with(
        "sess", rules, subagent_rules
    )
    assert session.transport_state == {
        "agent": "plan" if mode == "plan" else None,
        "pre_plan_mode": "allow" if mode == "plan" else None,
    }


@pytest.mark.asyncio
async def test_an_outside_switch_out_of_plan_restores_the_earlier_mode() -> None:
    plugin = OpenCodePlugin()
    session = _session(permission_mode="plan", transport_state={"agent": "plan"})
    runtime = _runtime(session)
    adapter = MagicMock(
        get_pre_plan_mode=MagicMock(return_value="ask"),
        set_session_permission=AsyncMock(return_value=True),
    )
    _live(plugin, runtime, session, adapter)

    await plugin._handle_agent_changed(runtime, "sess", "plan", "build")

    assert session.permission_mode == "ask"
    adapter.set_session_permission.assert_awaited_once_with("sess", ASK, ASK)
    runtime._record_system_event.assert_not_awaited()

    adapter.set_session_permission.return_value = False
    session.permission_mode = "plan"
    await plugin._handle_agent_changed(runtime, "sess", "plan", "build")
    assert "Could not apply the ask permission mode" in (
        runtime._record_system_event.await_args.args[1]
    )
    assert session.transport_state["agent"] is None
    assert runtime._emit_adapter_event.await_args.args[2] == "Exited plan mode"


# ── Approvals and questions ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("derived", "stored", "note"),
    [
        (SessionStatus.IDLE, SessionStatus.RUNNING, "Session idle"),
        (SessionStatus.INTERRUPTED, SessionStatus.RUNNING, "Turn interrupted"),
        (SessionStatus.RUNNING, SessionStatus.RUNNING, None),
        (None, SessionStatus.RUNNING, None),
    ],
)
@pytest.mark.asyncio
async def test_a_status_written_after_the_turn_ended_is_corrected(
    derived: SessionStatus | None, stored: SessionStatus, note: str | None
) -> None:
    plugin = OpenCodePlugin()
    session = _session(status=stored)
    runtime = _runtime(session)
    _live(
        plugin,
        runtime,
        session,
        MagicMock(session_status=MagicMock(return_value=derived)),
    )

    await plugin.post_approval(runtime, session)

    if note is None:
        runtime._record_system_event.assert_not_awaited()
    else:
        runtime._record_system_event.assert_awaited_once_with(
            "sess", note, status=derived
        )


@pytest.mark.asyncio
async def test_an_answer_records_the_status_the_turn_is_in() -> None:
    plugin = OpenCodePlugin()
    session = _session(status=SessionStatus.WAITING_INPUT)
    runtime = _runtime(session)
    runtime.questions = MagicMock()
    adapter = MagicMock(
        answer_question=AsyncMock(return_value=True),
        session_status=MagicMock(return_value=SessionStatus.IDLE),
    )
    _live(plugin, runtime, session, adapter)
    answers = [{"question": "Q", "answer": "Red"}]

    await plugin.answer_question(runtime, session, "Red", "frm_1", answers)

    adapter.answer_question.assert_awaited_once_with("sess", "frm_1", "Red", answers)
    assert session.status == SessionStatus.IDLE
    assert runtime._record_user_event.await_args.kwargs["status"] == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_declining_a_question_without_a_server_fails() -> None:
    plugin = OpenCodePlugin()
    session = _session()

    outcome = await plugin.decline_question(_runtime(session), session, "frm_1")

    assert outcome is QuestionDecline.FAILED


# ── Transport ───────────────────────────────────────────────────────────


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[object, ...]]] = []

    async def send_input(
        self, session_id: str, text: str, attachments: object = None
    ) -> None:
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
        if decision == "perhaps":
            raise OpenCodeError("unsupported permission decision: perhaps")
        self.calls.append(("respond_to_permission", session_id, (decision,)))
        return True

    def has_pending_approval(self, session_id: str) -> bool:
        self.calls.append(("has_pending_approval", session_id, ()))
        return True


@pytest.mark.asyncio
async def test_transport_reaches_the_server_of_a_session_with_launch_env() -> None:
    plugin = OpenCodePlugin()
    adapter = _FakeAdapter()
    runtime = _runtime()
    session = _session(cwd="/tmp", launch_env={"OPENCODE_CONFIG_DIR": "/cfg"})
    _live(plugin, runtime, session, adapter)
    transport = OpenCodeTransport(runtime=runtime, plugin=plugin)

    await transport.send_input(session, "hello")
    await transport.interrupt(session)
    await transport.respond_to_approval(session, "approve", None)

    assert transport.has_pending_approval(session)
    assert [call[0] for call in adapter.calls] == [
        "send_input",
        "interrupt",
        "respond_to_permission",
        "has_pending_approval",
    ]
    assert len(plugin._adapters) == 1
    with pytest.raises(HTTPException) as refused:
        await transport.respond_to_approval(session, "perhaps", None)
    assert refused.value.status_code == 400


@pytest.mark.asyncio
async def test_transport_routes_calls_by_session_launch_target() -> None:
    plugin = OpenCodePlugin()
    default_adapter = _FakeAdapter()
    remote_adapter = _FakeAdapter()
    plugin._adapters = cast(
        Any,
        {
            (None, "/tmp", (), ()): default_adapter,
            ("ssh-1", "/tmp", (), ()): remote_adapter,
        },
    )
    transport = OpenCodeTransport(runtime=_runtime(), plugin=plugin)
    session = _session(id="sess-remote", cwd="/tmp", launch_target_id="ssh-1")

    await transport.send_input(session, "hello")
    await transport.interrupt(session)
    await transport.terminate(session)
    assert await transport.respond_to_approval(session, "approve", None) is True

    assert transport.has_pending_approval(session) is True
    assert [call[0] for call in remote_adapter.calls] == [
        "send_input",
        "interrupt",
        "terminate_session",
        "respond_to_permission",
        "has_pending_approval",
    ]
    assert default_adapter.calls == []


# ── Threads and adapters ────────────────────────────────────────────────


class _DeleteAdapter:
    def __init__(self, deletable: set[str]) -> None:
        self.deletable = deletable
        self.calls: list[str] = []

    async def delete_session(self, session_id: str) -> bool:
        self.calls.append(session_id)
        return session_id in self.deletable


@pytest.mark.asyncio
async def test_delete_thread_routes_to_launch_target_adapter() -> None:
    plugin = OpenCodePlugin()
    local = _DeleteAdapter({"sess-local"})
    remote = _DeleteAdapter({"sess-remote"})
    plugin._adapters = cast(
        Any,
        {
            (None, "/tmp", (), ()): local,
            ("ssh-1", "/tmp", (), ()): remote,
        },
    )

    assert await plugin.delete_thread(cast(Any, None), "sess-remote", "ssh-1") is True
    assert remote.calls == ["sess-remote"]
    # Adapters for other launch targets are never consulted.
    assert local.calls == []


@pytest.mark.parametrize(
    ("first_has", "second_calls", "deleted"),
    [(set(), ["sess"], True), ({"sess"}, [], True)],
)
@pytest.mark.asyncio
async def test_delete_thread_tries_adapters_until_one_deletes(
    first_has: set[str], second_calls: list[str], deleted: bool
) -> None:
    plugin = OpenCodePlugin()
    first = _DeleteAdapter(first_has)
    second = _DeleteAdapter({"sess"})
    plugin._adapters = cast(
        Any, {(None, "/a", (), ()): first, (None, "/b", (), ()): second}
    )

    assert await plugin.delete_thread(cast(Any, None), "sess", None) is deleted
    assert first.calls == ["sess"]
    assert second.calls == second_calls


@pytest.mark.asyncio
async def test_delete_thread_returns_false_when_no_adapter_has_it() -> None:
    plugin = OpenCodePlugin()
    only = _DeleteAdapter(set())
    plugin._adapters = cast(Any, {(None, "/tmp", (), ()): only})

    assert await plugin.delete_thread(cast(Any, None), "missing", None) is False
    assert only.calls == ["missing"]


class _BuiltAdapter:
    def __init__(self, emit_event: Any, **kwargs: Any) -> None:
        self.workdir = kwargs["workdir"]
        self.kwargs = kwargs


@pytest.mark.asyncio
async def test_get_or_create_adapter_keys_by_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = OpenCodePlugin()
    runtime = _runtime()
    runtime._find_launch_target = lambda _id: SimpleNamespace(default_cwd="~/project")
    monkeypatch.setattr(
        "waypoint.backends.opencode.plugin.OpenCodeAdapter", _BuiltAdapter
    )

    async def build(target: str | None, cwd: str) -> Any:
        return await plugin._get_or_create_adapter(runtime, target, cwd)

    first, again, other, slashed = (
        await build("ssh-1", "/tmp/a"),
        await build("ssh-1", "/tmp/a/"),
        await build("ssh-1", "/tmp/b"),
        await build(None, "/tmp/a"),
    )

    assert first is again and first is not other
    assert (first.workdir, other.workdir) == ("/tmp/a", "/tmp/b")
    # Servers of one target share its database, and so its migration gate.
    assert first.kwargs["migration_gate"] is other.kwargs["migration_gate"]
    assert first.kwargs["migration_gate"] is not slashed.kwargs["migration_gate"]


@pytest.mark.asyncio
async def test_list_threads_merges_dedupes_and_hides_imported() -> None:
    plugin = OpenCodePlugin()

    def thread(thread_id: str, updated: int, title: str | None = "T") -> Any:
        return {
            "id": thread_id,
            "title": title,
            "location": {"directory": "/repo"},
            "time": {"created": 1, "updated": updated},
        }

    def adapter(*threads: Any) -> Any:
        return SimpleNamespace(list_sessions=AsyncMock(return_value=list(threads)))

    plugin._adapters = cast(
        Any,
        {
            ("ssh-1", "/a", (), ()): adapter(thread("ses_1", 22), thread("ses_2", 5)),
            ("ssh-1", "/b", (), ()): adapter(
                thread("ses_1", 44), thread("ses_3", 66, None)
            ),
        },
    )
    imported = _session(
        launch_target_id="ssh-1",
        transport_state={"opencode_session_id": "ses_1"},
    )

    threads = await plugin.list_threads(_runtime(imported), launch_target_id="ssh-1")

    assert [item.model_dump(mode="json") for item in threads] == [
        {
            "id": "ses_3",
            "title": "Untitled",
            "directory": "/repo",
            "created_at": 1,
            "updated_at": 66,
        },
        {
            "id": "ses_2",
            "title": "T",
            "directory": "/repo",
            "created_at": 1,
            "updated_at": 5,
        },
    ]


def _approval(request_id: str) -> EventRecord:
    return EventRecord(
        session_id="sess",
        ts=datetime.now(UTC),
        kind=EventKind.APPROVAL_REQUEST,
        text="t",
        metadata={"approval_id": request_id},
        sequence=1,
    )


def _restorable(status: SessionStatus) -> MagicMock:
    return MagicMock(
        restore_session=AsyncMock(),
        set_pre_plan_mode=MagicMock(),
        session_status=MagicMock(return_value=status),
    )


@pytest.mark.parametrize("path", ["restore", "reconnect"])
@pytest.mark.asyncio
async def test_restore_records_the_servers_status(path: str) -> None:
    plugin = OpenCodePlugin()
    session = _session(
        transport_state={
            "opencode_session_id": "ses_1",
            "agent": "plan",
            "pre_plan_mode": "ask",
        },
        permission_mode="plan",
        model="opencode/big-pickle",
        effort="high",
    )
    runtime = _runtime(session)
    runtime.storage.approval_events = [_approval("per_1")]
    runtime.storage.open_questions = ["frm_1"]
    adapter = _restorable(SessionStatus.WAITING_INPUT)
    key = plugin._session_adapter_key(runtime, session)
    plugin._adapters[key] = adapter

    if path == "restore":
        await plugin.restore_session(runtime, session)
    else:
        plugin._reconnect_targets[key] = {"sess"}
        await plugin._restore_after_reconnect(runtime, key)

    adapter.restore_session.assert_awaited_once_with(
        "sess",
        "ses_1",
        permissions=[],
        subagent_permissions=ASK,
        model="opencode/big-pickle",
        agent="plan",
        effort="high",
        open_approvals=["per_1"],
        open_questions=["frm_1"],
    )
    adapter.set_pre_plan_mode.assert_called_once_with("sess", "ask")
    assert session.status == SessionStatus.WAITING_INPUT
    assert (
        runtime._record_system_event.await_args.kwargs["status"]
        == SessionStatus.WAITING_INPUT
    )
    runtime.questions.reconcile.assert_awaited_once_with("sess")


@pytest.mark.asyncio
async def test_fork_writes_the_modes_rules_and_keeps_plan_state(
    tmp_path: Path,
) -> None:
    plugin = OpenCodePlugin()
    session = _session(
        transport_state={
            "opencode_session_id": "ses_parent",
            "agent": "plan",
            "pre_plan_mode": "allow",
        },
        permission_mode="ask",
        launch_env={"SECRET_TOKEN": "sk-oc"},
    )
    adapter = MagicMock(
        fork_session=AsyncMock(return_value="ses_forked"),
        set_pre_plan_mode=MagicMock(),
    )
    build = AsyncMock(return_value=adapter)
    cast(Any, plugin)._get_or_create_adapter = build

    forked = await plugin.fork_session(
        _runtime(session),
        session,
        new_session_id="forked",
        title="Forked",
        raw_log=tmp_path / "raw.log",
        structured_log=tmp_path / "events.jsonl",
    )

    adapter.fork_session.assert_awaited_once_with("forked", "ses_parent", ASK, ASK)
    adapter.set_pre_plan_mode.assert_called_once_with("forked", "allow")
    assert forked.transport_state == {
        "opencode_session_id": "ses_forked",
        "agent": "plan",
        "pre_plan_mode": "allow",
    }
    # The child keeps the source's private launch env, and its server is keyed
    # with that environment.
    assert forked.launch_env == {"SECRET_TOKEN": "sk-oc"}
    assert build.await_args is not None
    assert build.await_args.kwargs["launch_env"] == {"SECRET_TOKEN": "sk-oc"}


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.asyncio
async def test_import_keys_the_server_by_the_sessions_directory(
    tmp_path: Path, live: bool
) -> None:
    plugin = OpenCodePlugin()
    info = {"id": "ses_1", "title": "T", "location": {"directory": "/repo/actual"}}
    driver = _restorable(SessionStatus.IDLE)
    driver.get_session = AsyncMock(return_value=info)
    if live:
        fetcher = SimpleNamespace(get_session=AsyncMock(return_value=info))
        plugin._adapters = cast(Any, {("ssh-1", "/already", (), ()): fetcher})
    built: list[tuple[str | None, str | None, dict[str, str] | None]] = []

    async def build(
        runtime: Any, target: str | None, cwd: str | None, **kwargs: Any
    ) -> Any:
        built.append((target, cwd, kwargs.get("launch_env")))
        return driver

    cast(Any, plugin)._get_or_create_adapter = build
    runtime = _runtime()
    runtime._agent_process_env = lambda _backend, env: {**env, "RUNTIME": "1"}
    runtime._generate_session_id = lambda backend: f"{backend}-1"
    runtime._session_dir = lambda _sid: tmp_path
    runtime.seed_thread_history = AsyncMock()
    request = SimpleNamespace(
        thread_id="ses_1",
        launch_target_id="ssh-1",
        cwd="/repo/requested",
        import_history=False,
        launch_env={"USER": "1"},
    )

    result = await plugin.import_thread(runtime, cast(Any, request))

    assert (result.cwd, result.launch_target_id) == ("/repo/actual", "ssh-1")
    env = {"USER": "1", "RUNTIME": "1"}
    fetch = [] if live else [("ssh-1", "/repo/requested", env)]
    assert built == [*fetch, ("ssh-1", "/repo/actual", env)]


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

    await plugin.shutdown(cast(Any, SimpleNamespace()))

    assert cancelled.is_set()
    assert plugin._pending_tasks == set()
    assert plugin._shutting_down is True


def test_resume_slash_command_removed() -> None:
    names = {cmd.name for cmd in OPENCODE_SLASH_COMMANDS}
    assert "resume" not in names
    assert "compact" in names


@pytest.mark.asyncio
async def test_agent_launch_contract() -> None:
    from waypoint.backends.base import AgentLaunchContract

    plugin = OpenCodePlugin()

    assert isinstance(plugin, AgentLaunchContract)
    # The TUI runs its own server, so its tools see the session's environment.
    assert plugin.launch_flags(model="foo", permission_mode="ask") == ["--standalone"]
    assert plugin.pregenerate_thread_id() is None
    assert plugin.resume_args("123", ["a", "b"]) == ["a", "b"]
    assert await plugin.conversation_exists("thread", "/cwd", None) is False


@pytest.mark.asyncio
async def test_a_questions_change_reaches_the_tracker_and_the_asks_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = OpenCodePlugin()
    runtime = _runtime()
    monkeypatch.setattr(
        "waypoint.backends.opencode.plugin.OpenCodeAdapter", _BuiltAdapter
    )

    adapter = cast(Any, await plugin._get_or_create_adapter(runtime, None, "/tmp/a"))
    adapter.kwargs["on_questions_changed"]("sess-1")

    runtime.questions.mark_dirty.assert_called_once_with("sess-1")
    runtime.asks.mark_dirty.assert_called_once_with("sess-1")
