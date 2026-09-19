"""Unit tests for the backend-neutral ``capture_host_files`` sink that turns a
SendUserFile tool call's host paths into pinned session attachments."""

import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from waypoint.attachments import AttachmentStore
from waypoint.runtime import SessionRuntime
from waypoint.schemas import EventKind, EventRecord, SessionStatus


def _fake_runtime(
    tmp_path: Path, *, max_upload_bytes: int = 25 * 1024 * 1024, session: Any = ...
) -> SimpleNamespace:
    store = AttachmentStore(tmp_path / "attachments")
    if session is ...:
        session = SimpleNamespace(worktree_path=None, cwd=str(tmp_path))
    fake = SimpleNamespace(
        attachments=store,
        settings=SimpleNamespace(max_upload_bytes=max_upload_bytes),
        storage=SimpleNamespace(get_session=lambda _sid: session),
    )
    fake._persist_host_files = types.MethodType(
        SessionRuntime._persist_host_files, fake
    )
    return fake


def _persist(fake: SimpleNamespace, base: str | None, raw: list[Any]) -> list[Any]:
    return SessionRuntime._persist_host_files(
        cast(SessionRuntime, fake), "sess-1", base, raw
    )


async def _capture(fake: SimpleNamespace, metadata: dict[str, Any]) -> None:
    await SessionRuntime._capture_host_files(
        cast(SessionRuntime, fake), "sess-1", metadata
    )


def test_persist_absolute_and_relative(tmp_path: Path) -> None:
    fake = _fake_runtime(tmp_path)
    (tmp_path / "abs.txt").write_text("A")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "rel.txt").write_text("BB")

    specs = _persist(fake, str(tmp_path), [str(tmp_path / "abs.txt"), "sub/rel.txt"])

    names = {s.filename for s in specs}
    assert names == {"abs.txt", "rel.txt"}
    # Both are pinned (survive the orphan sweep).
    assert fake.attachments.pinned_ids("sess-1") == {s.id for s in specs}


def test_persist_dedupes_by_resolved_path(tmp_path: Path) -> None:
    fake = _fake_runtime(tmp_path)
    (tmp_path / "dup.txt").write_text("x")

    specs = _persist(
        fake, str(tmp_path), ["dup.txt", str(tmp_path / "dup.txt"), "./dup.txt"]
    )

    assert len(specs) == 1


def test_persist_skips_missing_and_oversized(tmp_path: Path) -> None:
    fake = _fake_runtime(tmp_path, max_upload_bytes=4)
    (tmp_path / "big.bin").write_bytes(b"toolarge")
    (tmp_path / "ok.txt").write_text("ok")

    specs = _persist(fake, str(tmp_path), ["nope.txt", "big.bin", "ok.txt"])

    assert [s.filename for s in specs] == ["ok.txt"]


def test_persist_ignores_non_string_entries(tmp_path: Path) -> None:
    fake = _fake_runtime(tmp_path)
    (tmp_path / "real.txt").write_text("r")

    specs = _persist(fake, str(tmp_path), [None, 3, "", "real.txt"])

    assert [s.filename for s in specs] == ["real.txt"]


@pytest.mark.asyncio
async def test_capture_sets_attachments_and_removes_key(tmp_path: Path) -> None:
    fake = _fake_runtime(tmp_path)
    (tmp_path / "doc.md").write_text("hello")
    metadata: dict[str, Any] = {
        "tool_name": "SendUserFile",
        "capture_host_files": ["doc.md"],
    }

    await _capture(fake, metadata)

    assert "capture_host_files" not in metadata
    assert isinstance(metadata["attachments"], list)
    assert metadata["attachments"][0]["filename"] == "doc.md"


@pytest.mark.asyncio
async def test_capture_no_attachments_when_all_unreadable(tmp_path: Path) -> None:
    fake = _fake_runtime(tmp_path)
    metadata: dict[str, Any] = {"capture_host_files": ["ghost.txt"]}

    await _capture(fake, metadata)

    assert "capture_host_files" not in metadata
    assert "attachments" not in metadata


@pytest.mark.asyncio
async def test_capture_uses_worktree_over_cwd(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "w.txt").write_text("w")
    session = SimpleNamespace(worktree_path=str(worktree), cwd=str(tmp_path))
    fake = _fake_runtime(tmp_path, session=session)
    metadata: dict[str, Any] = {"capture_host_files": ["w.txt"]}

    await _capture(fake, metadata)

    assert metadata["attachments"][0]["filename"] == "w.txt"


@pytest.mark.asyncio
async def test_capture_missing_session_is_noop(tmp_path: Path) -> None:
    fake = _fake_runtime(tmp_path, session=None)
    metadata: dict[str, Any] = {"capture_host_files": ["whatever.txt"]}

    await _capture(fake, metadata)

    assert "capture_host_files" not in metadata
    assert "attachments" not in metadata


@pytest.mark.asyncio
async def test_emit_adapter_event_captures_for_non_tool_call_kind(
    tmp_path: Path,
) -> None:
    # The capture seam was broadened from TOOL_CALL-only to any adapter event
    # carrying the transient key, so a task-notification SYSTEM_NOTE captures its
    # report attachment. This drives the real _emit_adapter_event gate.
    fake = _fake_runtime(tmp_path)
    (tmp_path / "report.md").write_text("the full report")
    persisted: list[EventRecord] = []

    def _append(event: EventRecord) -> EventRecord:
        persisted.append(event)
        return event

    fake.storage = SimpleNamespace(
        get_session=lambda _sid: SimpleNamespace(worktree_path=None, cwd=str(tmp_path)),
        next_sequence=lambda _sid: 1,
        append_event=_append,
    )
    fake.notifications = None
    fake._append_structured_log = lambda _sid, _ev: None

    async def _publish(_event: EventRecord) -> None:
        return None

    fake._publish_event = _publish
    fake._capture_host_files = types.MethodType(
        SessionRuntime._capture_host_files, fake
    )

    metadata: dict[str, Any] = {
        "method": "claude.task_notification",
        "capture_host_files": [str(tmp_path / "report.md")],
    }
    await SessionRuntime._emit_adapter_event(
        cast(SessionRuntime, fake),
        "sess-1",
        EventKind.SYSTEM_NOTE,
        "Agent finished",
        metadata,
        SessionStatus.RUNNING,
    )

    assert len(persisted) == 1
    saved = persisted[0].metadata
    assert "capture_host_files" not in saved
    assert saved["attachments"][0]["filename"] == "report.md"


# ─── inline blob capture ───


async def _capture_inline(fake: SimpleNamespace, metadata: dict[str, Any]) -> None:
    fake._persist_inline_blobs = types.MethodType(
        SessionRuntime._persist_inline_blobs, fake
    )
    await SessionRuntime._capture_inline_blobs(
        cast(SessionRuntime, fake), "sess-1", metadata
    )


async def test_inline_blobs_are_saved_and_pinned(tmp_path: Path) -> None:
    fake = _fake_runtime(tmp_path)
    metadata: dict[str, Any] = {
        "capture_inline_blobs": [
            {"filename": "task-1-result.txt", "text": "the tail", "mime": "text/plain"}
        ]
    }

    await _capture_inline(fake, metadata)

    assert "capture_inline_blobs" not in metadata
    (spec,) = metadata["attachments"]
    assert spec["filename"] == "task-1-result.txt"
    assert metadata["inline_attachment_ids"] == [spec["id"]]
    assert fake.attachments.pinned_ids("sess-1") == {spec["id"]}
    resolved = fake.attachments.resolve("sess-1", spec["id"])
    assert resolved is not None
    assert resolved[1].read_text(encoding="utf-8") == "the tail"


async def test_inline_blobs_append_to_existing_attachments(tmp_path: Path) -> None:
    fake = _fake_runtime(tmp_path)
    metadata: dict[str, Any] = {
        "attachments": [{"id": "pre-existing", "filename": "report.md"}],
        "capture_inline_blobs": [{"filename": "spill.txt", "text": "x"}],
    }

    await _capture_inline(fake, metadata)

    ids = [entry["id"] for entry in metadata["attachments"]]
    assert ids[0] == "pre-existing"
    assert len(ids) == 2
    # Only the spill is marked inline, so a consumer can still find the report.
    assert metadata["inline_attachment_ids"] == [ids[1]]


async def test_inline_blobs_refuse_oversized_text(tmp_path: Path) -> None:
    fake = _fake_runtime(tmp_path, max_upload_bytes=16)
    metadata: dict[str, Any] = {
        "capture_inline_blobs": [{"filename": "big.txt", "text": "y" * 64}]
    }

    await _capture_inline(fake, metadata)

    assert "attachments" not in metadata
    assert metadata["inline_capture_failed"] is True


async def test_inline_blobs_skip_malformed_entries(tmp_path: Path) -> None:
    fake = _fake_runtime(tmp_path)
    metadata: dict[str, Any] = {
        "capture_inline_blobs": ["nope", {"text": "no filename"}, {"filename": "a.txt"}]
    }

    await _capture_inline(fake, metadata)

    assert "attachments" not in metadata
    assert metadata["inline_capture_failed"] is True


@pytest.mark.asyncio
async def test_emit_adapter_event_never_persists_transient_capture_keys(
    tmp_path: Path,
) -> None:
    # A transient capture key is an input to the sinks, never event content. A
    # malformed one whose sink saved nothing must still be stripped, or the full
    # body it carries is written to SQLite and broadcast to every client.
    fake = _fake_runtime(tmp_path)
    persisted: list[EventRecord] = []

    def _append(event: EventRecord) -> EventRecord:
        persisted.append(event)
        return event

    fake.storage = SimpleNamespace(
        get_session=lambda _sid: SimpleNamespace(worktree_path=None, cwd=str(tmp_path)),
        next_sequence=lambda _sid: 1,
        append_event=_append,
    )
    fake.notifications = None
    fake._append_structured_log = lambda _sid, _ev: None

    async def _publish(_event: EventRecord) -> None:
        return None

    fake._publish_event = _publish
    fake._capture_host_files = types.MethodType(
        SessionRuntime._capture_host_files, fake
    )
    fake._capture_inline_blobs = types.MethodType(
        SessionRuntime._capture_inline_blobs, fake
    )
    fake._persist_inline_blobs = types.MethodType(
        SessionRuntime._persist_inline_blobs, fake
    )

    metadata: dict[str, Any] = {
        "method": "claude.task_notification",
        "capture_host_files": [str(tmp_path / "gone.md")],
        "capture_inline_blobs": [{"filename": "spill.txt", "text": "the whole tail"}],
    }
    await SessionRuntime._emit_adapter_event(
        cast(SessionRuntime, fake),
        "sess-1",
        EventKind.SYSTEM_NOTE,
        "Agent finished",
        metadata,
        SessionStatus.RUNNING,
    )

    saved = persisted[0].metadata
    assert "capture_host_files" not in saved
    assert "capture_inline_blobs" not in saved
    assert saved["attachments"][0]["filename"] == "spill.txt"


# ─── host text capture ───


async def _capture_text(fake: SimpleNamespace, metadata: dict[str, Any]) -> None:
    fake._read_host_text = types.MethodType(SessionRuntime._read_host_text, fake)
    await SessionRuntime._capture_host_text(
        cast(SessionRuntime, fake), "sess-1", metadata
    )


def _text_runtime(tmp_path: Path, inline_limit: int = 4 * 1024) -> SimpleNamespace:
    fake = _fake_runtime(tmp_path)
    fake.settings = SimpleNamespace(
        max_upload_bytes=25 * 1024 * 1024,
        inline_capture_max_bytes=inline_limit,
    )
    return fake


async def test_small_report_is_inlined_and_never_attached(tmp_path: Path) -> None:
    fake = _text_runtime(tmp_path)
    report = tmp_path / "run.output"
    report.write_text("all checks passed\n[exited with code 0]", encoding="utf-8")
    metadata: dict[str, Any] = {"capture_host_text": [str(report)]}

    await _capture_text(fake, metadata)

    assert "capture_host_text" not in metadata
    # Renderable in full, so there is nothing for a link or a fetch to add.
    assert "attachments" not in metadata
    assert metadata["captured_text"] == [
        {"filename": "run.output", "text": "all checks passed\n[exited with code 0]"}
    ]


async def test_oversized_report_falls_back_to_an_attachment(tmp_path: Path) -> None:
    fake = _text_runtime(tmp_path, inline_limit=32)
    report = tmp_path / "big.output"
    report.write_text("z" * 4096, encoding="utf-8")
    metadata: dict[str, Any] = {"capture_host_text": [str(report)]}

    await _capture_text(fake, metadata)

    assert "captured_text" not in metadata
    (spec,) = metadata["attachments"]
    assert spec["filename"] == "big.output"
    assert fake.attachments.pinned_ids("sess-1") == {spec["id"]}


async def test_binary_report_falls_back_to_an_attachment(tmp_path: Path) -> None:
    fake = _text_runtime(tmp_path)
    report = tmp_path / "shot.bin"
    report.write_bytes(b"\x89PNG\x00\r\n")
    metadata: dict[str, Any] = {"capture_host_text": [str(report)]}

    await _capture_text(fake, metadata)

    assert "captured_text" not in metadata
    assert len(metadata["attachments"]) == 1


async def test_missing_report_yields_nothing(tmp_path: Path) -> None:
    fake = _text_runtime(tmp_path)
    metadata: dict[str, Any] = {"capture_host_text": [str(tmp_path / "gone.output")]}

    await _capture_text(fake, metadata)

    assert "captured_text" not in metadata
    assert "attachments" not in metadata


async def test_capture_inlines_only_within_the_eager_budget(tmp_path: Path) -> None:
    # The budget bounds what every client receives whether or not the card is
    # expanded, so it tracks the body cap, not the on-demand preview ceiling.
    fake = _text_runtime(tmp_path)
    report = tmp_path / "run.output"
    report.write_text("y" * 8192, encoding="utf-8")
    metadata: dict[str, Any] = {"capture_host_text": [str(report)]}

    await _capture_text(fake, metadata)

    assert "captured_text" not in metadata
    assert metadata["attachments"][0]["size"] == 8192
