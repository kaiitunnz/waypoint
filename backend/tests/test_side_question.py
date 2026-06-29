"""Tests for backends/claude_code/side_question.py.

Covers the durable-state helpers, public API functions, background worker
logic, and the post-restart recovery sweep.  The tests are unit-level: they
replace the actual ``claude`` process calls with synchronous or async fakes
and verify the side-effects on storage and the broadcast hub.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import waypoint.backends.claude_code.side_question as sq_module
from waypoint.backends.claude_code.side_question import (
    MAX_ATTEMPTS,
    _parse_one_shot_output,
    _read_side_questions,
    _write_side_questions,
    dismiss_aside,
    fork_aside,
    recover_pending_side_questions,
    start_side_question,
)
from waypoint.schemas import (
    SessionEnvelope,
    SessionRecord,
    SessionSource,
    SessionStatus,
    SideQuestion,
    SideQuestionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage

# ---------------------------------------------------------------------------
# Minimal fakes
# ---------------------------------------------------------------------------


class _FakeBroadcast:
    def __init__(self) -> None:
        self.published: list[tuple[SessionEnvelope, str | None]] = []

    async def publish(
        self, message: SessionEnvelope, session_id: str | None = None
    ) -> None:
        self.published.append((message, session_id))


class _FakeRuntime:
    """Minimal SessionRuntime stub: real Storage, fake broadcast."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.broadcast = _FakeBroadcast()
        self._launch_targets: dict[str, Any] = {}
        # (session_id, kind, text) for events emitted via the runtime hooks.
        self.emitted: list[tuple[str, str, str]] = []

    def get_session(self, session_id: str) -> SessionRecord:
        session = self.storage.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def _find_launch_target(self, launch_target_id: str | None) -> Any:
        if not launch_target_id:
            return None
        return self._launch_targets.get(launch_target_id)

    async def _record_user_event(
        self,
        session_id: str,
        text: str,
        submit: bool,
        status: SessionStatus = SessionStatus.RUNNING,
        extra_metadata: dict | None = None,
        attachments: Any = None,
    ) -> None:
        self.emitted.append((session_id, "user_input", text))

    async def _emit_adapter_event(
        self,
        session_id: str,
        kind: Any,
        text: str,
        metadata: dict,
        status: SessionStatus,
    ) -> None:
        self.emitted.append((session_id, str(kind), text))


class _FakeAdapter:
    def __init__(self) -> None:
        self.restore_calls: list[tuple[str, str, str]] = []
        self.raise_on_restore: Exception | None = None

    async def restore_session(
        self,
        session_id: str,
        cwd: str,
        thread_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if self.raise_on_restore is not None:
            raise self.raise_on_restore
        self.restore_calls.append((session_id, cwd, thread_id))


class _FakePlugin:
    id = "claude_code"
    transport_id = "claude_cli"
    _counter = 0

    def __init__(self) -> None:
        self.adapter: _FakeAdapter | None = _FakeAdapter()

    def generate_session_id(self) -> str:
        _FakePlugin._counter += 1
        return f"fake-uuid-{_FakePlugin._counter}"

    def remote_executable(self, launch_target: Any) -> str:
        return "claude"

    def launch_factory(self, runtime: Any, launch_target_id: str | None) -> Any:
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_storage(tmp_path: Path) -> Storage:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    return Storage(settings.database_path)


@pytest.fixture
def runtime(tmp_storage: Storage) -> Any:
    return _FakeRuntime(tmp_storage)


@pytest.fixture
def plugin() -> Any:
    _FakePlugin._counter = 0
    return _FakePlugin()


def _make_session(
    storage: Storage,
    *,
    session_id: str = "sess-1",
    backend: str = "claude_code",
    thread_id: str | None = "thread-abc",
    status: SessionStatus = SessionStatus.IDLE,
    launch_target_id: str | None = None,
    transport_state_extra: dict | None = None,
) -> SessionRecord:
    settings_dir = storage.database_path.parent / "sessions" / session_id
    settings_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    state: dict = {}
    if thread_id is not None:
        state["thread_id"] = thread_id
    if transport_state_extra:
        state.update(transport_state_extra)
    session = SessionRecord(
        id=session_id,
        backend=backend,
        source=SessionSource.MANAGED,
        transport="claude_cli",
        title="Test Session",
        cwd="/tmp/project",
        launch_target_id=launch_target_id,
        status=status,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        transport_state=state,
        raw_log_path=str(settings_dir / "raw.log"),
        structured_log_path=str(settings_dir / "events.jsonl"),
    )
    return storage.create_session(session)


# ---------------------------------------------------------------------------
# _parse_one_shot_output
# ---------------------------------------------------------------------------


def test_parse_one_shot_success() -> None:
    raw = json.dumps(
        {"type": "result", "subtype": "success", "is_error": False, "result": "Hello!"}
    ).encode()
    assert _parse_one_shot_output(raw) == "Hello!"


def test_parse_one_shot_error_flag() -> None:
    raw = json.dumps(
        {"type": "result", "subtype": "error_max_turns", "is_error": True}
    ).encode()
    with pytest.raises(RuntimeError, match="claude error"):
        _parse_one_shot_output(raw)


def test_parse_one_shot_invalid_json() -> None:
    with pytest.raises(RuntimeError, match="not valid JSON"):
        _parse_one_shot_output(b"not json at all")


def test_parse_one_shot_missing_result_field() -> None:
    raw = json.dumps({"type": "result", "is_error": False}).encode()
    with pytest.raises(RuntimeError, match="missing or not a string"):
        _parse_one_shot_output(raw)


# ---------------------------------------------------------------------------
# _read_side_questions / _write_side_questions
# ---------------------------------------------------------------------------


def test_read_side_questions_empty(runtime: Any) -> None:
    session = _make_session(runtime.storage)
    assert _read_side_questions(session) == []


def test_write_and_read_round_trip(runtime: Any) -> None:
    session = _make_session(runtime.storage)
    sq = SideQuestion(
        id="sq-1",
        question="What is the plan?",
        status=SideQuestionStatus.PENDING,
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])
    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    loaded = _read_side_questions(fresh)
    assert len(loaded) == 1
    assert loaded[0].id == "sq-1"
    assert loaded[0].status == SideQuestionStatus.PENDING


# ---------------------------------------------------------------------------
# start_side_question
# ---------------------------------------------------------------------------


async def test_start_side_question_creates_pending_record(
    runtime: Any,
    plugin: Any,
) -> None:
    session = _make_session(runtime.storage, thread_id="thread-abc")

    await start_side_question(runtime, plugin, session, "What branch am I on?")

    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    qs = _read_side_questions(fresh)
    assert len(qs) == 1
    assert qs[0].status == SideQuestionStatus.PENDING
    assert qs[0].question == "What branch am I on?"
    assert qs[0].attempts == 1


async def test_start_side_question_broadcasts_upsert(
    runtime: Any,
    plugin: Any,
) -> None:
    session = _make_session(runtime.storage, thread_id="thread-abc")

    await start_side_question(runtime, plugin, session, "Quick q?")

    # Cancel the spawned bg task so it doesn't linger.
    for task in list(sq_module._background_tasks):
        task.cancel()

    messages = [m for m, _ in runtime.broadcast.published]
    assert any(m.type == "side_question" for m in messages)
    upsert = next(m for m in messages if m.type == "side_question")
    assert "side_question" in upsert.payload


async def test_start_side_question_no_thread_resolves_to_error(
    runtime: Any,
    plugin: Any,
) -> None:
    session = _make_session(runtime.storage, thread_id=None)

    await start_side_question(runtime, plugin, session, "What?")

    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    qs = _read_side_questions(fresh)
    assert len(qs) == 1
    assert qs[0].status == SideQuestionStatus.ERROR
    assert qs[0].error is not None
    # No background task should have been scheduled.
    assert not any(
        t.get_name().startswith("side-question") for t in sq_module._background_tasks
    )


# ---------------------------------------------------------------------------
# dismiss_aside
# ---------------------------------------------------------------------------


async def test_dismiss_aside_drops_record_and_broadcasts(
    runtime: Any,
    plugin: Any,
) -> None:
    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-del",
        question="Old?",
        status=SideQuestionStatus.ANSWERED,
        answer="Yes.",
        fork_thread_id=None,
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    await dismiss_aside(runtime, plugin, session, "sq-del")

    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    assert _read_side_questions(fresh) == []

    messages = [m for m, _ in runtime.broadcast.published]
    remove_msgs = [m for m in messages if m.type == "side_question"]
    assert any("removed_id" in m.payload for m in remove_msgs)


async def test_dismiss_aside_noop_for_unknown_id(
    runtime: Any,
    plugin: Any,
) -> None:
    session = _make_session(runtime.storage, thread_id="thread-abc")

    # Should not raise
    await dismiss_aside(runtime, plugin, session, "nonexistent-id")

    assert runtime.broadcast.published == []


# ---------------------------------------------------------------------------
# fork_aside
# ---------------------------------------------------------------------------


async def _noop_bring_up(new_session: SessionRecord, fork_thread_id: str) -> None:
    return None


async def test_fork_aside_raises_404_for_unknown_question(
    runtime: Any,
    tmp_path: Path,
) -> None:
    from fastapi import HTTPException

    session = _make_session(runtime.storage, thread_id="thread-abc")
    session_dir = tmp_path / "new-sess"
    session_dir.mkdir()

    with pytest.raises(HTTPException) as exc_info:
        await fork_aside(
            runtime,
            session,
            "nonexistent",
            new_session_id="new-sess",
            transport_id="claude_cli",
            title="Fork",
            raw_log=session_dir / "raw.log",
            structured_log=session_dir / "events.jsonl",
            bring_up=_noop_bring_up,
        )
    assert exc_info.value.status_code == 404


async def test_fork_aside_raises_400_when_no_fork_thread(
    runtime: Any,
    tmp_path: Path,
) -> None:
    from fastapi import HTTPException

    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-pending",
        question="Not answered yet?",
        status=SideQuestionStatus.PENDING,
        fork_thread_id=None,
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])
    session_dir = tmp_path / "new-sess"
    session_dir.mkdir()

    with pytest.raises(HTTPException) as exc_info:
        await fork_aside(
            runtime,
            session,
            "sq-pending",
            new_session_id="new-sess",
            transport_id="claude_cli",
            title="Fork",
            raw_log=session_dir / "raw.log",
            structured_log=session_dir / "events.jsonl",
            bring_up=_noop_bring_up,
        )
    assert exc_info.value.status_code == 400


async def test_fork_aside_drops_record_and_brings_up_new_session(
    runtime: Any,
    tmp_path: Path,
) -> None:
    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-ready",
        question="What files changed?",
        status=SideQuestionStatus.ANSWERED,
        answer="Only foo.py.",
        fork_thread_id="fork-uuid-xyz",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    session_dir = tmp_path / "new-sess"
    session_dir.mkdir()
    bring_up_calls: list[tuple[str, str]] = []

    async def _record(new_session: SessionRecord, fork_thread_id: str) -> None:
        bring_up_calls.append((new_session.id, fork_thread_id))

    new_sess = await fork_aside(
        runtime,
        session,
        "sq-ready",
        new_session_id="new-sess",
        transport_id="claude_cli",
        title="Forked question",
        raw_log=session_dir / "raw.log",
        structured_log=session_dir / "events.jsonl",
        bring_up=_record,
    )

    # Original record was dropped.
    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    assert _read_side_questions(fresh) == []

    # New session was created on the requested transport, resuming fork-uuid-xyz.
    assert new_sess.id == "new-sess"
    assert new_sess.transport == "claude_cli"
    assert new_sess.transport_state.get("thread_id") == "fork-uuid-xyz"

    # bring_up received the new record and the fork thread id.
    assert bring_up_calls == [("new-sess", "fork-uuid-xyz")]

    # The aside's question and answer were injected into the new transcript.
    injected = [
        (kind, text) for sid, kind, text in runtime.emitted if sid == "new-sess"
    ]
    assert ("user_input", "What files changed?") in injected
    assert ("agent_output", "Only foo.py.") in injected

    # Removal broadcast was sent.
    msgs = [m for m, _ in runtime.broadcast.published]
    assert any("removed_id" in m.payload for m in msgs if m.type == "side_question")


async def test_fork_aside_rolls_back_when_bring_up_fails(
    runtime: Any,
    tmp_path: Path,
) -> None:
    from fastapi import HTTPException

    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-ready",
        question="x?",
        status=SideQuestionStatus.ANSWERED,
        answer="y.",
        fork_thread_id="fork-uuid",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])
    session_dir = tmp_path / "new"
    session_dir.mkdir()

    async def _boom(new_session: SessionRecord, fork_thread_id: str) -> None:
        raise RuntimeError("launch failed")

    with pytest.raises(HTTPException) as exc_info:
        await fork_aside(
            runtime,
            session,
            "sq-ready",
            new_session_id="new-sess",
            transport_id="claude_cli",
            title="F",
            raw_log=session_dir / "raw.log",
            structured_log=session_dir / "events.jsonl",
            bring_up=_boom,
        )
    assert exc_info.value.status_code == 400

    # The half-created target is deleted, not left behind as an ERROR row.
    assert runtime.storage.get_session("new-sess") is None

    # The source aside is restored so the card returns and can be retried.
    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    restored = _read_side_questions(fresh)
    assert [q.id for q in restored] == ["sq-ready"]
    assert restored[0].fork_thread_id == "fork-uuid"

    # The card was broadcast removed (claim) then re-broadcast (rollback).
    sq_msgs = [m for m, _ in runtime.broadcast.published if m.type == "side_question"]
    assert any("removed_id" in m.payload for m in sq_msgs)
    assert any(m.payload.get("side_question") for m in sq_msgs)


async def test_fork_aside_deletes_orphan_fork_when_source_gone_on_rollback(
    runtime: Any,
    tmp_path: Path,
) -> None:
    """If the source session is deleted mid-promotion and the launch then fails,
    the fork thread has no owner to return to, so rollback must delete it."""
    from fastapi import HTTPException

    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-ready",
        question="x?",
        status=SideQuestionStatus.ANSWERED,
        answer="y.",
        fork_thread_id="fork-uuid",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])
    session_dir = tmp_path / "new"
    session_dir.mkdir()

    async def _boom(new_session: SessionRecord, fork_thread_id: str) -> None:
        # The source session vanishes while the launch is in flight.
        runtime.storage.delete_session(session.id)
        raise RuntimeError("launch failed")

    deleted_forks: list[str] = []
    with (
        pytest.raises(HTTPException),
        patch.object(
            sq_module,
            "_delete_fork_file",
            new=AsyncMock(side_effect=lambda fid, *a, **kw: deleted_forks.append(fid)),
        ),
    ):
        await fork_aside(
            runtime,
            session,
            "sq-ready",
            new_session_id="new-sess",
            transport_id="claude_cli",
            title="F",
            raw_log=session_dir / "raw.log",
            structured_log=session_dir / "events.jsonl",
            bring_up=_boom,
        )

    # Orphaned fork thread was deleted; neither session row survives.
    assert "fork-uuid" in deleted_forks
    assert runtime.storage.get_session(session.id) is None
    assert runtime.storage.get_session("new-sess") is None


async def test_fork_aside_rolls_back_on_pre_launch_failure(
    runtime: Any,
    tmp_path: Path,
) -> None:
    """A failure in post-claim setup (before bring_up) rolls back too: the target
    is deleted and the source aside restored, and bring_up never runs."""
    from fastapi import HTTPException

    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-ready",
        question="x?",
        status=SideQuestionStatus.ANSWERED,
        answer="y.",
        fork_thread_id="fork-uuid",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])
    session_dir = tmp_path / "new"
    session_dir.mkdir()

    bring_up_called: list[bool] = []

    async def _bring_up(new_session: SessionRecord, fork_thread_id: str) -> None:
        bring_up_called.append(True)

    with (
        pytest.raises(HTTPException),
        patch.object(
            sq_module,
            "_ingest_aside_qa",
            new=AsyncMock(side_effect=RuntimeError("seed failed")),
        ),
    ):
        await fork_aside(
            runtime,
            session,
            "sq-ready",
            new_session_id="new-sess",
            transport_id="claude_cli",
            title="F",
            raw_log=session_dir / "raw.log",
            structured_log=session_dir / "events.jsonl",
            bring_up=_bring_up,
        )

    assert bring_up_called == []  # failed before launch
    assert runtime.storage.get_session("new-sess") is None  # target rolled back
    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    assert [q.id for q in _read_side_questions(fresh)] == ["sq-ready"]


# ---------------------------------------------------------------------------
# _run_side_question_bg (via monkeypatching _run_one_shot_local)
# ---------------------------------------------------------------------------


async def test_bg_task_success_updates_record(
    runtime: Any,
    plugin: Any,
) -> None:
    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-bg",
        question="What's the status?",
        status=SideQuestionStatus.PENDING,
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    with patch.object(
        sq_module,
        "_run_one_shot_local",
        new=AsyncMock(return_value="All green!"),
    ):
        await sq_module._run_side_question_bg(runtime, plugin, session.id, "sq-bg")

    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    qs = _read_side_questions(fresh)
    assert len(qs) == 1
    assert qs[0].status == SideQuestionStatus.ANSWERED
    assert qs[0].answer == "All green!"
    assert qs[0].fork_thread_id is not None


async def test_bg_task_persists_fork_id_before_one_shot(
    runtime: Any,
    plugin: Any,
) -> None:
    """The fork id is recorded before the one-shot runs, so a restart mid-flight
    can find and delete the orphaned <E>.jsonl rather than leaking it."""
    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-pre",
        question="q?",
        status=SideQuestionStatus.PENDING,
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    seen: dict[str, Any] = {}

    async def fake_one_shot(
        question: str, thread_id: str, fork_id: str, cwd: str, *a: Any, **kw: Any
    ) -> str:
        fresh = runtime.storage.get_session(session.id)
        seen["recorded"] = _read_side_questions(fresh)[0].fork_thread_id
        seen["arg"] = fork_id
        return "answer"

    with patch.object(
        sq_module, "_run_one_shot_local", new=AsyncMock(side_effect=fake_one_shot)
    ):
        await sq_module._run_side_question_bg(runtime, plugin, session.id, "sq-pre")

    # While the one-shot was still running, the record already carried the fork id.
    assert seen["recorded"] is not None
    assert seen["recorded"] == seen["arg"]


async def test_bg_task_marks_error_on_failure(
    runtime: Any,
    plugin: Any,
) -> None:
    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-fail",
        question="Will fail?",
        status=SideQuestionStatus.PENDING,
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    with (
        patch.object(
            sq_module,
            "_run_one_shot_local",
            new=AsyncMock(side_effect=RuntimeError("claude not found")),
        ),
        patch.object(sq_module, "_delete_fork_file", new=AsyncMock()),
    ):
        await sq_module._run_side_question_bg(runtime, plugin, session.id, "sq-fail")

    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    qs = _read_side_questions(fresh)
    assert len(qs) == 1
    assert qs[0].status == SideQuestionStatus.ERROR
    assert "claude not found" in (qs[0].error or "")


async def test_bg_task_cleans_up_fork_file_when_dismissed_mid_run(
    runtime: Any,
    plugin: Any,
) -> None:
    """If the side-question is dismissed while the bg task is running, the
    completed fork file must be cleaned up."""
    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-race",
        question="Race?",
        status=SideQuestionStatus.PENDING,
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    cleanup_calls: list[str] = []

    async def fake_delete(fork_thread_id: str, *args: Any, **kwargs: Any) -> None:
        cleanup_calls.append(fork_thread_id)

    async def fake_one_shot(*args: Any, **kwargs: Any) -> str:
        # Dismiss the record while the "one-shot" is running.
        _write_side_questions(runtime, session.id, [])
        return "answer"

    with (
        patch.object(
            sq_module, "_run_one_shot_local", new=AsyncMock(side_effect=fake_one_shot)
        ),
        patch.object(
            sq_module, "_delete_fork_file", new=AsyncMock(side_effect=fake_delete)
        ),
    ):
        await sq_module._run_side_question_bg(runtime, plugin, session.id, "sq-race")

    # The fork file must have been cleaned up (dismissed-while-running path).
    assert len(cleanup_calls) == 1


async def test_bg_task_noop_when_session_gone(
    runtime: Any,
    plugin: Any,
) -> None:
    # Session never stored — bg task must silently exit.
    with patch.object(
        sq_module, "_run_one_shot_local", new=AsyncMock(return_value="x")
    ):
        await sq_module._run_side_question_bg(
            runtime, plugin, "nonexistent-sess", "sq-x"
        )


# ---------------------------------------------------------------------------
# recover_pending_side_questions
# ---------------------------------------------------------------------------


async def test_recover_re_issues_pending_records(
    runtime: Any,
    plugin: Any,
) -> None:
    session = _make_session(runtime.storage, thread_id="t1")
    sq = SideQuestion(
        id="sq-pend",
        question="Pending?",
        status=SideQuestionStatus.PENDING,
        fork_thread_id="stale-fork",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    cleanup_calls: list[str] = []

    with (
        patch.object(
            sq_module,
            "_delete_fork_file",
            new=AsyncMock(side_effect=lambda fid, *a, **kw: cleanup_calls.append(fid)),
        ),
        patch.object(sq_module, "_schedule_bg_task"),
    ):
        await recover_pending_side_questions(runtime, plugin)

    # Stale fork file deleted.
    assert "stale-fork" in cleanup_calls

    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    qs = _read_side_questions(fresh)
    assert len(qs) == 1
    assert qs[0].attempts == 2
    assert qs[0].resumed is True
    assert qs[0].fork_thread_id is None
    assert qs[0].status == SideQuestionStatus.PENDING


async def test_recover_caps_at_max_attempts(
    runtime: Any,
    plugin: Any,
) -> None:
    session = _make_session(runtime.storage, thread_id="t1")
    sq = SideQuestion(
        id="sq-cap",
        question="Capped?",
        status=SideQuestionStatus.PENDING,
        fork_thread_id=None,
        attempts=MAX_ATTEMPTS,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    with patch.object(sq_module, "_delete_fork_file", new=AsyncMock()):
        await recover_pending_side_questions(runtime, plugin)

    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    qs = _read_side_questions(fresh)
    assert len(qs) == 1
    assert qs[0].status == SideQuestionStatus.ERROR


async def test_recover_rebroadcasts_answered_records(
    runtime: Any,
    plugin: Any,
) -> None:
    session = _make_session(runtime.storage, thread_id="t1")
    sq = SideQuestion(
        id="sq-ans",
        question="Done?",
        status=SideQuestionStatus.ANSWERED,
        answer="Yes.",
        fork_thread_id="survived-fork",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    await recover_pending_side_questions(runtime, plugin)

    # No deletion for answered records.
    msgs = [m for m, _ in runtime.broadcast.published]
    upserts = [
        m for m in msgs if m.type == "side_question" and "side_question" in m.payload
    ]
    assert len(upserts) == 1
    assert upserts[0].payload["side_question"]["id"] == "sq-ans"


async def test_recover_cleans_up_dead_sessions(
    runtime: Any,
    plugin: Any,
) -> None:
    session = _make_session(
        runtime.storage, thread_id="t1", status=SessionStatus.EXITED
    )
    sq = SideQuestion(
        id="sq-dead",
        question="Dead?",
        status=SideQuestionStatus.ANSWERED,
        fork_thread_id="dangling-fork",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    cleanup_calls: list[str] = []

    with patch.object(
        sq_module,
        "_delete_fork_file",
        new=AsyncMock(side_effect=lambda fid, *a, **kw: cleanup_calls.append(fid)),
    ):
        await recover_pending_side_questions(runtime, plugin)

    assert "dangling-fork" in cleanup_calls

    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    assert _read_side_questions(fresh) == []


async def test_recover_skips_non_claude_sessions(
    runtime: Any,
    plugin: Any,
) -> None:
    # A codex session — the plugin id doesn't match; should be ignored.
    session = _make_session(
        runtime.storage,
        session_id="codex-sess",
        backend="codex",
        thread_id="t1",
    )
    sq = SideQuestion(
        id="sq-x",
        question="Ignored?",
        status=SideQuestionStatus.PENDING,
        fork_thread_id="fork-x",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    # Manually inject the record (bypasses _write_side_questions so we avoid
    # having to set the transport correctly for codex).
    state = {
        **session.transport_state,
        "pending_side_questions": [sq.model_dump(mode="json")],
    }
    runtime.storage.update_session(session.id, transport_state=state)

    cleanup_calls: list[str] = []
    with patch.object(
        sq_module,
        "_delete_fork_file",
        new=AsyncMock(side_effect=lambda fid, *a, **kw: cleanup_calls.append(fid)),
    ):
        await recover_pending_side_questions(runtime, plugin)

    # Nothing touched.
    assert cleanup_calls == []


async def test_recover_covers_extra_backend_ids(
    runtime: Any,
    plugin: Any,
) -> None:
    """Legacy backend=claude_tty rows are skipped by the default claude_code
    sweep but recovered when their id is passed in ``backend_ids``."""
    session = _make_session(
        runtime.storage,
        session_id="claude_tty-x",
        backend="claude_tty",
        thread_id="t1",
    )
    sq = SideQuestion(
        id="sq-tty",
        question="q?",
        status=SideQuestionStatus.PENDING,
        fork_thread_id="fork-tty",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    state = {
        **session.transport_state,
        "pending_side_questions": [sq.model_dump(mode="json")],
    }
    runtime.storage.update_session(session.id, transport_state=state)

    # Default sweep (claude_code only) ignores the claude_tty row.
    with (
        patch.object(sq_module, "_schedule_bg_task") as sched_default,
        patch.object(sq_module, "_delete_fork_file", new=AsyncMock()),
    ):
        await recover_pending_side_questions(runtime, plugin)
    sched_default.assert_not_called()

    # Scoped to claude_tty it re-issues: deletes the stale fork and reschedules.
    cleanup: list[str] = []
    with (
        patch.object(sq_module, "_schedule_bg_task") as sched_tty,
        patch.object(
            sq_module,
            "_delete_fork_file",
            new=AsyncMock(side_effect=lambda fid, *a, **kw: cleanup.append(fid)),
        ),
    ):
        await recover_pending_side_questions(
            runtime, plugin, backend_ids={"claude_tty"}
        )
    sched_tty.assert_called_once()
    assert "fork-tty" in cleanup


# ---------------------------------------------------------------------------
# Fix 1: --tools "" disables tools in one-shot argv
# ---------------------------------------------------------------------------


async def test_one_shot_local_argv_disables_tools(
    runtime: Any,
    plugin: Any,
) -> None:
    """The local one-shot must pass --tools "" so Claude cannot call any tool."""
    session = _make_session(runtime.storage, thread_id="thread-abc")
    _write_side_questions(
        runtime,
        session.id,
        [
            SideQuestion(
                id="sq-tools",
                question="What is 2+2?",
                status=SideQuestionStatus.PENDING,
                attempts=1,
                created_at=datetime.now(UTC),
            )
        ],
    )

    captured_args: list[list[str]] = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.append(list(args))

        class _FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                import json as _json

                return (
                    _json.dumps(
                        {"is_error": False, "result": "4", "subtype": "success"}
                    ).encode(),
                    b"",
                )

        return _FakeProc()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await sq_module._run_side_question_bg(runtime, plugin, session.id, "sq-tools")

    assert captured_args, "subprocess was never called"
    argv = captured_args[0]
    # --tools must appear in the argv and the next element must be the empty string.
    assert "--tools" in argv, f"--tools not in argv: {argv}"
    tools_idx = argv.index("--tools")
    assert (
        argv[tools_idx + 1] == ""
    ), f"--tools value is not empty: {argv[tools_idx + 1]!r}"


async def test_one_shot_remote_argv_disables_tools() -> None:
    """The remote one-shot command string must include --tools ''."""
    from waypoint.launch_targets import SshLaunchTargetConfig

    target = SshLaunchTargetConfig(id="box", name="Box", ssh_destination="user@host")
    captured_args: list[list[str]] = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.append(list(args))

        class _FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                import json as _json

                return (
                    _json.dumps(
                        {"is_error": False, "result": "ok", "subtype": "success"}
                    ).encode(),
                    b"",
                )

        return _FakeProc()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await sq_module._run_one_shot_remote(
            "What branch?", "thread-1", "fork-1", "~/project", target, "claude"
        )

    assert captured_args, "subprocess was never called"
    # The remote command is the last arg passed to ssh; the claude args are
    # embedded in it.  Check the full command string contains --tools.
    full_cmd = " ".join(captured_args[0])
    assert "--tools" in full_cmd, f"--tools not in remote command: {full_cmd!r}"


# ---------------------------------------------------------------------------
# Fix 2: recovery does not clobber a completion that lands mid-sweep
# ---------------------------------------------------------------------------


async def test_recover_does_not_clobber_mid_sweep_completion(
    runtime: Any,
    plugin: Any,
) -> None:
    """A bg-task completion that lands while recover_pending_side_questions is
    running (between the stale snapshot and the lock) must not be overwritten,
    and the completed fork file must not be deleted."""
    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-race",
        question="Will race?",
        status=SideQuestionStatus.PENDING,
        fork_thread_id="stale-E",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    deleted_files: list[str] = []

    async def fake_delete(fid: str, *args: Any, **kwargs: Any) -> None:
        # Simulate the bg task completing sq-race to ANSWERED just before
        # the recovery writes its update.
        if fid == "stale-E":
            completed = sq.model_copy(
                update={
                    "status": SideQuestionStatus.ANSWERED,
                    "answer": "Race answer.",
                    "fork_thread_id": "live-E",
                }
            )
            _write_side_questions(runtime, session.id, [completed])
        deleted_files.append(fid)

    with (
        patch.object(
            sq_module, "_delete_fork_file", new=AsyncMock(side_effect=fake_delete)
        ),
        patch.object(sq_module, "_schedule_bg_task"),
    ):
        await recover_pending_side_questions(runtime, plugin)

    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    qs = _read_side_questions(fresh)
    assert len(qs) == 1, "record was dropped"
    # The record must remain ANSWERED — NOT overwritten with pending/error.
    assert (
        qs[0].status == SideQuestionStatus.ANSWERED
    ), f"record was clobbered: {qs[0].status}"
    assert qs[0].fork_thread_id == "live-E", "live fork thread id was lost"
    # The live fork file must NOT have been deleted.
    assert "live-E" not in deleted_files, "live fork file was wrongly deleted"


# ---------------------------------------------------------------------------
# Fix 3: delete_session_side_questions
# ---------------------------------------------------------------------------


async def test_delete_session_side_questions_clears_records_and_fork_files(
    runtime: Any,
    plugin: Any,
) -> None:
    """delete_session_side_questions must delete every fork file and clear the
    pending_side_questions list from storage."""
    from waypoint.backends.claude_code.side_question import (
        delete_session_side_questions,
    )

    session = _make_session(runtime.storage, thread_id="thread-abc")
    qs_init = [
        SideQuestion(
            id="sq-a",
            question="A?",
            status=SideQuestionStatus.ANSWERED,
            fork_thread_id="fork-A",
            attempts=1,
            created_at=datetime.now(UTC),
        ),
        SideQuestion(
            id="sq-b",
            question="B?",
            status=SideQuestionStatus.PENDING,
            fork_thread_id="fork-B",
            attempts=1,
            created_at=datetime.now(UTC),
        ),
        SideQuestion(
            id="sq-c",
            question="C?",
            status=SideQuestionStatus.ERROR,
            fork_thread_id=None,
            attempts=1,
            created_at=datetime.now(UTC),
        ),
    ]
    _write_side_questions(runtime, session.id, qs_init)
    # Re-read so the snapshot passed to the function has the questions embedded.
    session_with_qs = runtime.storage.get_session(session.id)
    assert session_with_qs is not None

    deleted_files: list[str] = []
    with patch.object(
        sq_module,
        "_delete_fork_file",
        new=AsyncMock(side_effect=lambda fid, *a, **kw: deleted_files.append(fid)),
    ):
        await delete_session_side_questions(runtime, plugin, session_with_qs)

    # Both fork files must be deleted; sq-c had no fork file.
    assert set(deleted_files) == {"fork-A", "fork-B"}

    # Storage must have an empty pending_side_questions list.
    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    assert _read_side_questions(fresh) == []


async def test_delete_session_side_questions_uses_passed_snapshot(
    runtime: Any,
    plugin: Any,
) -> None:
    """Fork ids must be read from the passed session snapshot, not re-fetched
    from storage (storage may already be gone when called)."""
    from waypoint.backends.claude_code.side_question import (
        delete_session_side_questions,
    )

    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq_snap = SideQuestion(
        id="sq-snap",
        question="Snap?",
        status=SideQuestionStatus.ANSWERED,
        fork_thread_id="fork-snap",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    # Write to storage so the session exists there.
    _write_side_questions(runtime, session.id, [sq_snap])
    # Re-read the session snapshot with the fork id embedded.
    session_with_fork = runtime.storage.get_session(session.id)
    assert session_with_fork is not None

    # Now remove the session from storage (simulating post-delete call).
    runtime.storage.delete_session(session.id)

    deleted_files: list[str] = []
    with patch.object(
        sq_module,
        "_delete_fork_file",
        new=AsyncMock(side_effect=lambda fid, *a, **kw: deleted_files.append(fid)),
    ):
        # The session row is already gone; must still clean up the fork file.
        await delete_session_side_questions(runtime, plugin, session_with_fork)

    assert "fork-snap" in deleted_files


async def test_delete_session_side_questions_prefers_fresh_state(
    runtime: Any,
    plugin: Any,
) -> None:
    """If an aside was claimed by a concurrent fork-promotion (so the live row no
    longer lists it) the delete sweep must NOT delete its handed-off fork file,
    even though the passed snapshot still has it."""
    from waypoint.backends.claude_code.side_question import (
        delete_session_side_questions,
    )

    session = _make_session(runtime.storage, thread_id="thread-abc")
    sq = SideQuestion(
        id="sq-claimed",
        question="Q?",
        status=SideQuestionStatus.ANSWERED,
        fork_thread_id="fork-handed-off",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])
    # Snapshot still lists the aside...
    stale_snapshot = runtime.storage.get_session(session.id)
    assert stale_snapshot is not None
    # ...but a concurrent fork-promotion has since claimed it (row now empty).
    _write_side_questions(runtime, session.id, [])

    deleted_files: list[str] = []
    with patch.object(
        sq_module,
        "_delete_fork_file",
        new=AsyncMock(side_effect=lambda fid, *a, **kw: deleted_files.append(fid)),
    ):
        await delete_session_side_questions(runtime, plugin, stale_snapshot)

    # The handed-off fork file must survive — the promoted session owns it now.
    assert deleted_files == []


async def test_delete_session_side_questions_cleans_aside_absent_from_snapshot(
    runtime: Any,
    plugin: Any,
) -> None:
    """A /btw persisted after the delete-time snapshot (snapshot empty, live row
    has it) is still cleaned up — cleanup keys off fresh state, not the snapshot,
    so the plugin hook can safely call it unconditionally."""
    from waypoint.backends.claude_code.side_question import (
        delete_session_side_questions,
    )

    session = _make_session(runtime.storage, thread_id="thread-abc")
    empty_snapshot = runtime.storage.get_session(session.id)  # no asides yet
    assert empty_snapshot is not None
    # A /btw lands after the snapshot was captured.
    sq = SideQuestion(
        id="sq-late",
        question="late?",
        status=SideQuestionStatus.ANSWERED,
        fork_thread_id="fork-late",
        attempts=1,
        created_at=datetime.now(UTC),
    )
    _write_side_questions(runtime, session.id, [sq])

    deleted_files: list[str] = []
    with patch.object(
        sq_module,
        "_delete_fork_file",
        new=AsyncMock(side_effect=lambda fid, *a, **kw: deleted_files.append(fid)),
    ):
        await delete_session_side_questions(runtime, plugin, empty_snapshot)

    assert "fork-late" in deleted_files
    fresh = runtime.storage.get_session(session.id)
    assert fresh is not None
    assert _read_side_questions(fresh) == []
