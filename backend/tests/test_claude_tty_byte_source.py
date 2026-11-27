"""Unit tests for claude_tty transcript byte sources."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from waypoint.backends.claude_tty import byte_source as bs
from waypoint.backends.claude_tty.byte_source import (
    LocalTranscriptByteSource,
    RemoteClaudeTranscriptByteSource,
)
from waypoint.backends.transcript_fs_remote import RemoteRead
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus

TID = "11111111-1111-1111-1111-111111111111"
CWD = "/repo/app"
MOVED_CWD = "/repo/app/.claude/worktrees/w"


def _session(cwd: str = CWD) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id="s1",
        backend="claude_code",
        source=SessionSource.MANAGED,
        transport="claude_tty",
        title="t",
        cwd=cwd,
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/r",
        structured_log_path="/e",
        transport_state={"thread_id": TID},
    )


def _launch_target() -> SshLaunchTargetConfig:
    return SshLaunchTargetConfig(
        id="remote-1", name="Remote", ssh_destination="host", ssh_bin="/bin/sh"
    )


# ── Local source ─────────────────────────────────────────────────────────────


def _project_file(config_dir: Path, cwd: str = MOVED_CWD) -> Path:
    path = bs.transcript_path(cwd, TID, str(config_dir))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _local_source(config_dir: Path) -> tuple[LocalTranscriptByteSource, Path]:
    source = LocalTranscriptByteSource(CWD, TID, str(config_dir))
    return source, _project_file(config_dir, CWD)


def test_local_missing_file_is_unobserved(tmp_path: Path) -> None:
    src, _ = _local_source(tmp_path)
    read = src.read_from(0)
    assert read.observed is False


def test_local_reads_data_size_identity(tmp_path: Path) -> None:
    src, path = _local_source(tmp_path)
    path.write_bytes(b"line one\nline two\n")
    read = src.read_from(0)
    assert read.observed is True
    assert read.data == b"line one\nline two\n"
    assert read.size == 18
    st = path.stat()
    assert read.identity == (st.st_dev, st.st_ino)


def test_local_honors_offset(tmp_path: Path) -> None:
    src, path = _local_source(tmp_path)
    path.write_bytes(b"0123456789")
    read = src.read_from(4)
    assert read.data == b"456789"


def test_local_metadata_only_does_not_read_body(tmp_path: Path) -> None:
    src, path = _local_source(tmp_path)
    path.write_bytes(b"0123456789")
    read = src.read_from(0, metadata_only=True)
    assert read.observed is True
    assert read.data == b""
    assert read.size == 10
    assert read.identity is not None


# ── Local source: a transcript Claude moved ──────────────────────────────────


def test_local_follows_a_moved_transcript_from_the_same_offset(
    tmp_path: Path,
) -> None:
    src, path = _local_source(tmp_path)
    path.write_bytes(b"one\n")
    first = src.read_from(0)
    moved = _project_file(tmp_path)
    path.rename(moved)
    with moved.open("ab") as fh:
        fh.write(b"two\n")

    read = src.read_from(4)

    assert read.observed is True
    assert read.data == b"two\n"
    assert read.size == 8
    assert read.identity == first.identity


def test_local_search_runs_only_on_a_miss_and_backs_off(
    tmp_path: Path, monkeypatch
) -> None:
    now = {"t": 100.0}
    monkeypatch.setattr(bs.time, "monotonic", lambda: now["t"])
    searches: list[str] = []
    real = bs.local_claude_thread_artifacts

    def counting(thread_id: str, config_dir: str | None = None) -> list[Path]:
        searches.append(thread_id)
        return real(thread_id, config_dir)

    monkeypatch.setattr(bs, "local_claude_thread_artifacts", counting)
    src, path = _local_source(tmp_path)

    # No transcript yet: one search, then none until the backoff elapses.
    for _ in range(5):
        assert src.read_from(0).observed is False
    assert len(searches) == 1
    now["t"] += 1.0
    src.read_from(0)
    assert len(searches) == 2
    for _ in range(10):
        now["t"] += 20.0
        src.read_from(0)
    # The backoff is capped at 10 seconds.
    searches.clear()
    now["t"] += 9.0
    src.read_from(0)
    assert searches == []
    now["t"] += 1.0
    src.read_from(0)
    assert len(searches) == 1

    # Reading the file at its known path never searches.
    path.write_bytes(b"one\n")
    searches.clear()
    for _ in range(5):
        assert src.read_from(0).observed is True
    assert searches == []

    # The next miss searches at once.
    path.rename(_project_file(tmp_path))
    assert src.read_from(4).observed is True
    assert len(searches) == 1


def test_local_finds_a_transcript_that_reappears_after_the_backoff(
    tmp_path: Path, monkeypatch
) -> None:
    now = {"t": 100.0}
    monkeypatch.setattr(bs.time, "monotonic", lambda: now["t"])
    src, path = _local_source(tmp_path)
    path.write_bytes(b"one\n")
    src.read_from(0)
    parked = tmp_path / "parked"
    path.rename(parked)
    assert src.read_from(4).observed is False

    parked.rename(_project_file(tmp_path))
    assert src.read_from(4).observed is False  # still backing off
    now["t"] += 1.0
    read = src.read_from(4)
    assert read.observed is True
    assert read.data == b""
    assert read.size == 4


def test_local_forced_read_ignores_the_search_backoff(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 100.0)
    src, _ = _local_source(tmp_path)
    assert src.read_from(0).observed is False
    _project_file(tmp_path).write_bytes(b"tail\n")
    assert src.read_from(0).observed is False
    assert src.read_from(0, force=True).data == b"tail\n"


def test_local_replaced_path_prefers_the_file_it_was_reading(tmp_path: Path) -> None:
    src, path = _local_source(tmp_path)
    path.write_bytes(b"one\n")
    first = src.read_from(0)
    moved = _project_file(tmp_path)
    path.rename(moved)
    path.write_bytes(b"another file\n")

    read = src.read_from(4)

    assert read.identity == first.identity
    assert read.data == b""


def test_local_replaced_transcript_reports_the_new_identity(tmp_path: Path) -> None:
    src, path = _local_source(tmp_path)
    path.write_bytes(b"one\n")
    first = src.read_from(0)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"rewritten\n")
    replacement.replace(path)

    read = src.read_from(4)

    assert read.observed is True
    assert read.identity != first.identity


def test_local_skips_a_file_swapped_between_stat_and_open(
    tmp_path: Path, monkeypatch
) -> None:
    src, path = _local_source(tmp_path)
    path.write_bytes(b"one\n")
    other = tmp_path / "other"
    other.write_bytes(b"two\n")
    monkeypatch.setattr(bs.os, "fstat", lambda fd: other.stat())
    assert src.read_from(0).observed is False


def test_local_ambiguous_matches_stay_unobserved(tmp_path: Path) -> None:
    _project_file(tmp_path, "/one").write_bytes(b"a\n")
    _project_file(tmp_path, "/two").write_bytes(b"b\n")
    src, _ = _local_source(tmp_path)
    assert src.read_from(0).observed is False


# ── Remote source ────────────────────────────────────────────────────────────


class _FakeFs:
    def __init__(self) -> None:
        self.glob_result: list[str] = []
        self.read_result: RemoteRead | None = None
        # When set, reads are answered per path (a missing path reads as None).
        self.by_path: dict[str, RemoteRead] | None = None
        self.glob_calls = 0
        self.read_calls: list[tuple[str, int, int]] = []
        self.expand: dict[str, str] = {}

    def glob(self, root: str, pattern: str) -> list[str]:
        self.glob_calls += 1
        self.last_config_dir = root
        self.last_pattern = pattern
        return list(self.glob_result)

    def read_range(self, path: str, offset: int, limit: int) -> RemoteRead | None:
        self.read_calls.append((path, offset, limit))
        if self.by_path is not None:
            return self.by_path.get(path)
        return self.read_result

    def expanduser(self, path: str) -> str:
        return self.expand.get(path, path)


def _remote_source(
    fs: _FakeFs, session: SessionRecord, config_dir: str | None = None
) -> RemoteClaudeTranscriptByteSource:
    runtime = MagicMock()
    runtime.storage.get_session.return_value = session
    src = RemoteClaudeTranscriptByteSource(
        runtime, session.id, TID, _launch_target(), config_dir
    )
    src._fs = fs  # type: ignore[assignment]
    return src


def test_remote_discovery_miss_is_unobserved_steady(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 100.0)
    fs = _FakeFs()  # empty glob result
    src = _remote_source(fs, _session())
    read = src.read_from(0)
    assert read.observed is False
    assert fs.glob_calls == 1
    # steady cadence: next read ~1s out
    assert src._next_read_at == 101.0


def test_remote_resolves_then_reads(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    fs = _FakeFs()
    fs.glob_result = ["/home/u/.claude/projects/-repo-app/" + TID + ".jsonl"]
    fs.read_result = RemoteRead(data=b"hello\n", size=6, device=1, inode=2)
    src = _remote_source(fs, _session())
    read = src.read_from(0)
    assert read.observed is True
    assert read.data == b"hello\n"
    assert read.size == 6
    assert read.identity == (1, 2)
    # path cached: a second read does not re-glob
    src._next_read_at = 0.0
    src.read_from(6)
    assert fs.glob_calls == 1
    assert fs.read_calls[-1] == (fs.glob_result[0], 6, 256 * 1024)


def test_remote_resolves_its_own_thread_not_the_sessions(monkeypatch) -> None:
    # After a /clear rebind the session names the successor, but the old
    # tailer's source must keep reading the thread it was built for.
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    session = _session()
    session.transport_state["thread_id"] = "22222222-2222-2222-2222-222222222222"
    fs = _FakeFs()
    _remote_source(fs, session).read_from(0)
    assert fs.last_pattern == f"projects/*/{TID}.jsonl"


def test_remote_multi_match_refuses(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    fs = _FakeFs()
    fs.glob_result = ["/a/" + TID + ".jsonl", "/b/" + TID + ".jsonl"]
    src = _remote_source(fs, _session())
    read = src.read_from(0)
    assert read.observed is False
    assert src._path is None


def test_remote_read_none_backs_off(monkeypatch) -> None:
    t = {"now": 0.0}
    monkeypatch.setattr(bs.time, "monotonic", lambda: t["now"])
    fs = _FakeFs()
    fs.glob_result = ["/p/" + TID + ".jsonl"]
    fs.read_result = None  # read failure
    src = _remote_source(fs, _session())
    src.read_from(0)
    first = src._next_read_at
    t["now"] = first
    src.read_from(0)
    second = src._next_read_at
    # exponential: second interval strictly larger than the first
    assert (second - t["now"]) > (first - 0.0)


def test_remote_backoff_capped(monkeypatch) -> None:
    t = {"now": 0.0}
    monkeypatch.setattr(bs.time, "monotonic", lambda: t["now"])
    fs = _FakeFs()
    fs.glob_result = ["/p/" + TID + ".jsonl"]
    fs.read_result = None
    src = _remote_source(fs, _session())
    for _ in range(20):
        t["now"] = src._next_read_at
        src.read_from(0)
    assert src._backoff <= 10.0


def test_remote_cadence_throttles(monkeypatch) -> None:
    t = {"now": 0.0}
    monkeypatch.setattr(bs.time, "monotonic", lambda: t["now"])
    fs = _FakeFs()
    fs.glob_result = ["/p/" + TID + ".jsonl"]
    fs.read_result = RemoteRead(data=b"", size=0, device=1, inode=2)
    src = _remote_source(fs, _session())
    src.read_from(0)
    calls_after_first = len(fs.read_calls)
    # within the interval, a non-forced read is throttled (no SSH)
    t["now"] = src._next_read_at - 0.01
    read = src.read_from(0)
    assert read.observed is False
    assert len(fs.read_calls) == calls_after_first
    # force bypasses the gate
    read = src.read_from(0, force=True)
    assert read.observed is True
    assert len(fs.read_calls) == calls_after_first + 1


def test_remote_metadata_only_uses_zero_limit(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    fs = _FakeFs()
    fs.glob_result = ["/p/" + TID + ".jsonl"]
    fs.read_result = RemoteRead(data=b"", size=42, device=1, inode=2)
    src = _remote_source(fs, _session())
    read = src.read_from(0, metadata_only=True)
    assert read.observed is True
    assert read.size == 42
    assert fs.read_calls[-1][2] == 0  # limit


def test_remote_config_root_default(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    fs = _FakeFs()
    src = _remote_source(fs, _session(), config_dir=None)
    src.read_from(0)
    assert fs.last_config_dir == "~/.claude"


def test_remote_config_root_absolute(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    fs = _FakeFs()
    src = _remote_source(fs, _session(), config_dir="/home/u/.claude-work")
    src.read_from(0)
    assert fs.last_config_dir == "/home/u/.claude-work"


def test_remote_config_root_relative_joins_remote_cwd(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    fs = _FakeFs()
    fs.expand["~/repo"] = "/home/u/repo"
    src = _remote_source(fs, _session(cwd="~/repo"), config_dir=".claude-rel")
    src.read_from(0)
    assert fs.last_config_dir == "/home/u/repo/.claude-rel"


def test_remote_follows_a_moved_transcript(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    old, new = f"/p/old/{TID}.jsonl", f"/p/new/{TID}.jsonl"
    fs = _FakeFs()
    fs.glob_result = [old]
    fs.by_path = {old: RemoteRead(data=b"one\n", size=4, device=1, inode=2)}
    src = _remote_source(fs, _session())
    src.read_from(0)

    fs.glob_result = [new]
    fs.by_path = {new: RemoteRead(data=b"two\n", size=8, device=1, inode=2)}
    src._next_read_at = 0.0
    read = src.read_from(4)

    assert read.data == b"two\n"
    assert read.identity == (1, 2)
    assert fs.read_calls[-1] == (new, 4, 256 * 1024)
    assert src._backoff == 1.0


def test_remote_keeps_its_path_when_a_search_finds_nothing(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    old = f"/p/old/{TID}.jsonl"
    fs = _FakeFs()
    fs.glob_result = [old]
    fs.by_path = {old: RemoteRead(data=b"", size=4, device=1, inode=2)}
    src = _remote_source(fs, _session())
    src.read_from(0)

    fs.glob_result, fs.by_path = [], {}
    src._next_read_at = 0.0
    assert src.read_from(4).observed is False
    assert src._path == old
    assert src._backoff > 1.0


def test_remote_replaced_path_prefers_the_file_it_was_reading(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    old, new = f"/p/old/{TID}.jsonl", f"/p/new/{TID}.jsonl"
    original = RemoteRead(data=b"one\n", size=4, device=1, inode=2)
    fs = _FakeFs()
    fs.glob_result = [old]
    fs.by_path = {old: original}
    src = _remote_source(fs, _session())
    src.read_from(0)

    fs.glob_result = [old, new]
    fs.by_path = {
        old: RemoteRead(data=b"other", size=5, device=1, inode=9),
        new: original,
    }
    src._next_read_at = 0.0
    read = src.read_from(4)

    assert read.identity == (1, 2)
    assert src._path == new


def test_remote_relative_config_dir_stays_on_the_launch_folder(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    session = _session(cwd="/repo/app/.claude/worktrees/w")
    session.transport_state["launch_cwd"] = "/repo/app"
    fs = _FakeFs()
    _remote_source(fs, session, config_dir=".claude-rel").read_from(0)
    assert fs.last_config_dir == "/repo/app/.claude-rel"


def test_remote_exception_does_not_raise(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    fs = _FakeFs()

    def _boom(*a: object, **k: object) -> list[str]:
        raise RuntimeError("ssh exploded")

    fs.glob = _boom  # type: ignore[method-assign]
    src = _remote_source(fs, _session())
    read = src.read_from(0)  # must not raise
    assert read.observed is False
    assert src._backoff > 1.0  # error backoff engaged


# ── source selection at the plugin boundary ──────────────────────────────────


def _capture_source(monkeypatch, **start_kwargs) -> object:
    from waypoint.backends.claude_tty import plugin as plugin_mod

    captured: dict[str, object] = {}

    class _FakeTailer:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self) -> None:  # pragma: no cover - never awaited
            return None

    monkeypatch.setattr(plugin_mod, "TranscriptTailer", _FakeTailer)
    monkeypatch.setattr(plugin_mod.asyncio, "create_task", lambda coro: MagicMock())
    plugin = plugin_mod.ClaudeTtyPlugin()
    plugin._start_tailer(MagicMock(), "s1", TID, "/repo/app", **start_kwargs)
    return captured["source"]


def test_start_tailer_builds_local_source_without_target(monkeypatch) -> None:
    source = _capture_source(monkeypatch, config_dir="/cfg", launch_target=None)
    assert isinstance(source, bs.LocalTranscriptByteSource)


def test_start_tailer_builds_remote_source_with_target(monkeypatch) -> None:
    source = _capture_source(
        monkeypatch, config_dir="/home/u/.claude-work", launch_target=_launch_target()
    )
    assert isinstance(source, bs.RemoteClaudeTranscriptByteSource)
    assert source._config_dir == "/home/u/.claude-work"
    assert source._launch_target_id == "remote-1"
