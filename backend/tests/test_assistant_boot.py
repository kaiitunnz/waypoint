"""The personal assistant is set up beside the serving app."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from restore_harness import (
    add_assistant,
    add_session,
    assert_refused,
    assistant_settings,
    gated_restore,
    hang,
    make_runtime,
    raising_async,
    record_dispatches,
    started,
    stored,
    stub_launch,
    stub_restores,
    until,
)
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    SessionCreateRequest,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import AssistantConfig
from waypoint.storage import Storage

pytestmark = pytest.mark.usefixtures("quiet_warming")


def stub_assistant_creation(
    runtime: SessionRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating an assistant adds an idle one named ``fresh``."""

    async def create(*_args: Any, **_kwargs: Any) -> SessionRecord:
        add_assistant(runtime, "fresh")
        return stored(runtime, "fresh")

    monkeypatch.setattr(runtime, "_create_assistant_session", create)


async def test_assistant_creation_does_not_block_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("waypoint.assistant_boot.LAUNCH_STOP_GRACE_SECONDS", 0.05)
    runtime = make_runtime(tmp_path, **assistant_settings())
    monkeypatch.setattr(runtime, "_create_assistant_session", hang)

    async with started(runtime):
        await until(lambda: runtime.assistant_boot._launching)
        for request in (
            runtime.reset_assistant(),
            runtime.reattach_assistant(),
            runtime.terminate_assistant(),
        ):
            await assert_refused(request, 409, "still starting")


async def test_assistant_is_pinned_before_its_restore_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime)
    stub_restores(monkeypatch, {"assistant": hang})

    async with started(runtime):
        summary = runtime.assistant_summary()
        assert summary is not None
        assert summary.session_id == "assistant"
        await until(lambda: runtime.restores.is_restoring("assistant"))
        async with asyncio.timeout(2):
            summary = await runtime.terminate_assistant()
        assert summary.session_id == "assistant"
        assert summary.status == SessionStatus.EXITED


async def test_assistant_is_replaced_when_its_restore_ends_exited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime)
    release = asyncio.Event()

    async def exited(runtime: SessionRuntime, session: SessionRecord) -> None:
        await runtime._record_system_event(
            session.id, "No thread", status=SessionStatus.EXITED
        )

    stub_restores(monkeypatch, {"assistant": gated_restore(release, exited)})
    stub_assistant_creation(runtime, monkeypatch)

    async with started(runtime):
        assert runtime.assistant_session_id == "assistant"
        release.set()
        await until(lambda: runtime.assistant_session_id == "fresh")
        assert stored(runtime, "assistant").source == SessionSource.MANAGED


@pytest.mark.parametrize(
    ("transport", "replaced"), [("codex_app_server", False), ("tmux", True)]
)
async def test_errored_assistant_is_kept_unless_it_runs_in_a_pane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    replaced: bool,
) -> None:
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime, transport=transport, status=SessionStatus.ERROR)
    stub_assistant_creation(runtime, monkeypatch)

    await runtime._ensure_assistant_session()

    assert runtime.assistant_session_id == ("fresh" if replaced else "assistant")


async def test_the_thread_being_replaced_takes_no_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("waypoint.assistant_boot.LAUNCH_STOP_GRACE_SECONDS", 0.05)
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime, status=SessionStatus.EXITED)
    monkeypatch.setattr(runtime, "_create_assistant_session", hang)
    stub_restores(monkeypatch, {})
    sent = record_dispatches(runtime, monkeypatch)

    async with started(runtime):
        await until(lambda: runtime.assistant_boot._launching)
        await assert_refused(
            runtime.handle_input("assistant", SessionInputRequest(text="hi")),
            409,
            "still starting",
        )
        assert sent == []


async def test_failed_assistant_creation_keeps_the_previous_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime, status=SessionStatus.EXITED)
    monkeypatch.setattr(
        runtime, "_create_assistant_session", raising_async("cannot launch")
    )

    async with started(runtime):
        task = runtime.assistant_boot._task
        assert task is not None
        await asyncio.wait({task})
        assert runtime.assistant_session_id == "assistant"
        assert stored(runtime, "assistant").source == SessionSource.ASSISTANT


async def test_shutdown_lets_a_pane_assistant_launch_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(
        tmp_path, assistant=AssistantConfig(backend="codex", transport="tmux")
    )
    launching = asyncio.Event()

    async def create(
        _runtime: SessionRuntime, request: Any, *, session_id: str, **_kwargs: Any
    ) -> SessionRecord:
        launching.set()
        await asyncio.sleep(0.2)
        return add_session(runtime, session_id, transport="tmux", cwd=request.cwd)

    stub_launch(monkeypatch, "tmux", create)

    async with started(runtime):
        await launching.wait()

    [session] = Storage(runtime.settings.database_path).list_sessions()
    assert session.source == SessionSource.ASSISTANT


async def test_hung_paneless_launch_is_bounded_and_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)

    async def create(
        _runtime: SessionRuntime, _request: Any, *, session_id: str, **_kwargs: Any
    ) -> SessionRecord:
        add_session(runtime, session_id, status=SessionStatus.STARTING)
        return await hang()

    stub_launch(monkeypatch, "codex_app_server", create)

    with pytest.raises(TimeoutError):
        await runtime.create_session(
            SessionCreateRequest(
                backend="codex", transport="codex_app_server", cwd=str(tmp_path)
            ),
            launch_timeout=0.1,
        )

    [session] = runtime.storage.list_sessions()
    assert session.status == SessionStatus.ERROR
