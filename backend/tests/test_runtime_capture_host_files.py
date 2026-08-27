"""Unit tests for the backend-neutral ``capture_host_files`` sink that turns a
SendUserFile tool call's host paths into pinned session attachments."""

import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from waypoint.attachments import AttachmentStore
from waypoint.runtime import SessionRuntime


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
