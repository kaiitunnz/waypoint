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


def _session(cwd: str = "/repo/app") -> SessionRecord:
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


def test_local_missing_file_is_unobserved(tmp_path: Path) -> None:
    src = LocalTranscriptByteSource.__new__(LocalTranscriptByteSource)
    src._path = tmp_path / "nope.jsonl"
    read = src.read_from(0)
    assert read.observed is False


def test_local_reads_data_size_identity(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_bytes(b"line one\nline two\n")
    src = LocalTranscriptByteSource.__new__(LocalTranscriptByteSource)
    src._path = path
    read = src.read_from(0)
    assert read.observed is True
    assert read.data == b"line one\nline two\n"
    assert read.size == 18
    st = path.stat()
    assert read.identity == (st.st_dev, st.st_ino)


def test_local_honors_offset(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_bytes(b"0123456789")
    src = LocalTranscriptByteSource.__new__(LocalTranscriptByteSource)
    src._path = path
    read = src.read_from(4)
    assert read.data == b"456789"


def test_local_metadata_only_does_not_read_body(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_bytes(b"0123456789")
    src = LocalTranscriptByteSource.__new__(LocalTranscriptByteSource)
    src._path = path
    read = src.read_from(0, metadata_only=True)
    assert read.observed is True
    assert read.data == b""
    assert read.size == 10
    assert read.identity is not None


# ── Remote source ────────────────────────────────────────────────────────────


class _FakeFs:
    def __init__(self) -> None:
        self.glob_result: list[str] = []
        self.read_result: RemoteRead | None = None
        self.glob_calls = 0
        self.read_calls: list[tuple[str, int, int]] = []
        self.expand: dict[str, str] = {}

    def glob_artifacts(
        self, session: object, plugin: object, config_dir: str
    ) -> list[str]:
        self.glob_calls += 1
        self.last_config_dir = config_dir
        return list(self.glob_result)

    def read_range(self, path: str, offset: int, limit: int) -> RemoteRead | None:
        self.read_calls.append((path, offset, limit))
        return self.read_result

    def expanduser(self, path: str) -> str:
        return self.expand.get(path, path)


def _remote_source(
    fs: _FakeFs, session: SessionRecord, config_dir: str | None = None
) -> RemoteClaudeTranscriptByteSource:
    runtime = MagicMock()
    runtime.storage.get_session.return_value = session
    src = RemoteClaudeTranscriptByteSource(
        runtime, session.id, MagicMock(), _launch_target(), config_dir
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


def test_remote_exception_does_not_raise(monkeypatch) -> None:
    monkeypatch.setattr(bs.time, "monotonic", lambda: 0.0)
    fs = _FakeFs()

    def _boom(*a: object, **k: object) -> list[str]:
        raise RuntimeError("ssh exploded")

    fs.glob_artifacts = _boom  # type: ignore[method-assign]
    src = _remote_source(fs, _session())
    read = src.read_from(0)  # must not raise
    assert read.observed is False
    assert src._backoff > 1.0  # error backoff engaged
