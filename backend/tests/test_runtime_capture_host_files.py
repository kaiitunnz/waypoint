"""Unit tests for the backend-neutral capture seams that turn host paths and
in-memory text into pinned session attachments or inline event text."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from waypoint.attachments import AttachmentStore
from waypoint.runtime import SessionRuntime
from waypoint.schemas import AttachmentOrigin, EventKind, EventRecord, SessionStatus


def _runtime(
    tmp_path: Path,
    *,
    max_upload_bytes: int = 25 * 1024 * 1024,
    inline_capture_max_bytes: int = 4 * 1024,
    session: Any = ...,
) -> Any:
    """A runtime carrying only the attributes the capture path reads, so the
    real methods run against a real store."""
    runtime: Any = SessionRuntime.__new__(SessionRuntime)
    runtime.attachments = AttachmentStore(tmp_path / "attachments")
    runtime.settings = SimpleNamespace(
        max_upload_bytes=max_upload_bytes,
        inline_capture_max_bytes=inline_capture_max_bytes,
    )
    if session is ...:
        session = SimpleNamespace(worktree_path=None, cwd=str(tmp_path))
    runtime.storage = SimpleNamespace(get_session=lambda _sid: session)
    return runtime


def _emit_runtime(tmp_path: Path) -> tuple[Any, list[EventRecord]]:
    runtime = _runtime(tmp_path)
    persisted: list[EventRecord] = []

    def _append(event: EventRecord) -> EventRecord:
        persisted.append(event)
        return event

    async def _publish(_event: EventRecord) -> None:
        return None

    runtime.storage = SimpleNamespace(
        get_session=lambda _sid: SimpleNamespace(worktree_path=None, cwd=str(tmp_path)),
        next_sequence=lambda _sid: 1,
        append_event=_append,
    )
    runtime.notifications = None
    runtime._append_structured_log = lambda _sid, _ev: None
    runtime._publish_event = _publish
    return runtime, persisted


async def _emit(runtime: Any, metadata: dict[str, Any]) -> None:
    await runtime._emit_adapter_event(
        "sess-1", EventKind.SYSTEM_NOTE, "note", metadata, SessionStatus.RUNNING
    )


# ─── host files ───


def test_persist_absolute_and_relative(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    (tmp_path / "abs.txt").write_text("A")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "rel.txt").write_text("BB")

    specs = runtime._persist_host_files(
        "sess-1", str(tmp_path), [str(tmp_path / "abs.txt"), "sub/rel.txt"]
    )

    assert {s.filename for s in specs} == {"abs.txt", "rel.txt"}
    assert runtime.attachments.pinned_ids("sess-1") == {s.id for s in specs}


def test_persist_dedupes_by_resolved_path(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    (tmp_path / "one.txt").write_text("A")

    specs = runtime._persist_host_files(
        "sess-1", str(tmp_path), [str(tmp_path / "one.txt"), "one.txt"]
    )

    assert len(specs) == 1


def test_persist_skips_missing_and_oversized(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, max_upload_bytes=4)
    (tmp_path / "big.txt").write_text("way too long")

    specs = runtime._persist_host_files(
        "sess-1", str(tmp_path), [str(tmp_path / "big.txt"), str(tmp_path / "gone")]
    )

    assert specs == []


def test_persist_ignores_non_string_entries(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    assert runtime._persist_host_files("sess-1", str(tmp_path), [None, 3, ""]) == []


async def test_capture_sets_attachments(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    (tmp_path / "report.md").write_text("the report")
    metadata: dict[str, Any] = {}

    await runtime._capture_host_files("sess-1", [str(tmp_path / "report.md")], metadata)

    assert metadata["attachments"][0]["filename"] == "report.md"


async def test_capture_no_attachments_when_all_unreadable(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    metadata: dict[str, Any] = {}

    await runtime._capture_host_files("sess-1", [str(tmp_path / "gone")], metadata)

    assert "attachments" not in metadata


async def test_capture_uses_worktree_over_cwd(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "in-wt.txt").write_text("A")
    runtime = _runtime(
        tmp_path,
        session=SimpleNamespace(worktree_path=str(worktree), cwd=str(tmp_path)),
    )
    metadata: dict[str, Any] = {}

    await runtime._capture_host_files("sess-1", ["in-wt.txt"], metadata)

    assert metadata["attachments"][0]["filename"] == "in-wt.txt"


async def test_capture_missing_session_is_noop(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, session=None)
    metadata: dict[str, Any] = {}

    await runtime._capture_host_files("sess-1", ["relative.txt"], metadata)

    assert "attachments" not in metadata


# ─── host text ───


async def test_small_report_is_inlined_and_never_attached(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    report = tmp_path / "run.output"
    report.write_text("all checks passed\n[exited with code 0]", encoding="utf-8")
    metadata: dict[str, Any] = {}

    await runtime._capture_host_text("sess-1", [str(report)], metadata)

    assert "attachments" not in metadata
    assert metadata["captured_text"] == ["all checks passed\n[exited with code 0]"]


async def test_capture_inlines_only_within_the_eager_budget(tmp_path: Path) -> None:
    # The budget bounds what every client receives whether or not the card is
    # expanded, so it tracks the body cap, not the on-demand preview ceiling.
    runtime = _runtime(tmp_path)
    report = tmp_path / "run.output"
    report.write_text("y" * 8192, encoding="utf-8")
    metadata: dict[str, Any] = {}

    await runtime._capture_host_text("sess-1", [str(report)], metadata)

    assert "captured_text" not in metadata
    assert metadata["attachments"][0]["size"] == 8192


async def test_binary_report_falls_back_to_an_attachment(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    report = tmp_path / "shot.bin"
    report.write_bytes(b"\x89PNG\x00\r\n")
    metadata: dict[str, Any] = {}

    await runtime._capture_host_text("sess-1", [str(report)], metadata)

    assert "captured_text" not in metadata
    assert len(metadata["attachments"]) == 1


async def test_missing_report_yields_nothing(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    metadata: dict[str, Any] = {}

    await runtime._capture_host_text("sess-1", [str(tmp_path / "gone")], metadata)

    assert metadata == {}


# ─── inline blobs ───


async def test_inline_blobs_are_saved_and_pinned(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    metadata: dict[str, Any] = {}

    await runtime._capture_inline_blobs(
        "sess-1",
        [{"filename": "task-1-result.txt", "text": "the tail", "mime": "text/plain"}],
        metadata,
    )

    (spec,) = metadata["attachments"]
    assert spec["filename"] == "task-1-result.txt"
    assert metadata["inline_attachment_ids"] == [spec["id"]]
    assert runtime.attachments.pinned_ids("sess-1") == {spec["id"]}
    resolved = runtime.attachments.resolve("sess-1", spec["id"])
    assert resolved is not None
    assert resolved[1].read_text(encoding="utf-8") == "the tail"


async def test_inline_blobs_append_to_existing_attachments(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    metadata: dict[str, Any] = {
        "attachments": [{"id": "pre-existing", "filename": "report.md"}]
    }

    await runtime._capture_inline_blobs(
        "sess-1", [{"filename": "spill.txt", "text": "x"}], metadata
    )

    ids = [entry["id"] for entry in metadata["attachments"]]
    assert ids[0] == "pre-existing"
    # Only the spill is marked inline, so a consumer can still find the report.
    assert metadata["inline_attachment_ids"] == [ids[1]]


async def test_inline_blobs_refuse_oversized_text(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, max_upload_bytes=16)
    metadata: dict[str, Any] = {}

    await runtime._capture_inline_blobs(
        "sess-1", [{"filename": "big.txt", "text": "y" * 64}], metadata
    )

    assert metadata == {}


async def test_inline_blobs_skip_malformed_entries(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    metadata: dict[str, Any] = {}

    await runtime._capture_inline_blobs(
        "sess-1", ["nope", {"text": "no filename"}, {"filename": "a.txt"}], metadata
    )

    assert metadata == {}


# ─── the emit gate ───


async def test_emit_captures_for_non_tool_call_kind(tmp_path: Path) -> None:
    # The seam is reached by any adapter event carrying the transient key, not
    # only a TOOL_CALL, so a task notification captures its report.
    runtime, persisted = _emit_runtime(tmp_path)
    (tmp_path / "report.md").write_text("the full report")

    await _emit(
        runtime,
        {
            "method": "claude.task_notification",
            "capture_host_files": [str(tmp_path / "report.md")],
        },
    )

    saved = persisted[0].metadata
    assert "capture_host_files" not in saved
    assert saved["attachments"][0]["filename"] == "report.md"


async def test_emit_never_persists_transient_capture_keys(tmp_path: Path) -> None:
    # A transient key is an input to the sinks, never event content: one whose
    # sink saved nothing must still be stripped, or the body it carries reaches
    # SQLite and every client.
    runtime, persisted = _emit_runtime(tmp_path)

    await _emit(
        runtime,
        {
            "capture_host_files": [str(tmp_path / "gone.md")],
            "capture_host_text": [str(tmp_path / "gone.output")],
            "capture_inline_blobs": [{"filename": "spill.txt", "text": "the tail"}],
        },
    )

    saved = persisted[0].metadata
    assert not [key for key in saved if key.startswith("capture_")]
    assert saved["attachments"][0]["filename"] == "spill.txt"


async def test_emit_runs_both_capture_seams_into_one_event(tmp_path: Path) -> None:
    # A report and a spilled body can land on the same event; the inline id set
    # is what keeps them distinguishable.
    runtime, persisted = _emit_runtime(tmp_path)
    report = tmp_path / "big.output"
    report.write_text("z" * 8192, encoding="utf-8")

    await _emit(
        runtime,
        {
            "capture_host_text": [str(report)],
            "capture_inline_blobs": [{"filename": "spill.txt", "text": "the tail"}],
        },
    )

    saved = persisted[0].metadata
    assert [entry["filename"] for entry in saved["attachments"]] == [
        "big.output",
        "spill.txt",
    ]
    inline = set(saved["inline_attachment_ids"])
    reports = [a["filename"] for a in saved["attachments"] if a["id"] not in inline]
    assert reports == ["big.output"]


async def test_emit_tags_captures_with_their_origin(tmp_path: Path) -> None:
    runtime, persisted = _emit_runtime(tmp_path)
    report = tmp_path / "run.output"
    report.write_text("z" * 8192, encoding="utf-8")

    await _emit(
        runtime,
        {
            "capture_origin": "task_output",
            "capture_host_text": [str(report)],
            "capture_inline_blobs": [{"filename": "spill.txt", "text": "tail"}],
        },
    )

    metadata = persisted[0].metadata
    assert "capture_origin" not in metadata
    assert [spec["origin"] for spec in metadata["attachments"]] == [
        "task_output",
        "task_output",
    ]
    assert {spec.origin for spec, _ts in runtime.attachments.entries("sess-1")} == {
        AttachmentOrigin.TASK_OUTPUT
    }


async def test_emit_ignores_an_unknown_origin(tmp_path: Path) -> None:
    runtime, persisted = _emit_runtime(tmp_path)

    await _emit(
        runtime,
        {
            "capture_origin": "bogus",
            "capture_inline_blobs": [{"filename": "spill.txt", "text": "tail"}],
        },
    )

    (spec,) = persisted[0].metadata["attachments"]
    assert spec["origin"] is None
