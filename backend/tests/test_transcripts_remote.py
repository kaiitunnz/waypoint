"""RemoteTranscriptFilesystem against a fake command-capturing remote (no SSH).

Exercises RemoteTranscriptFilesystem's real argv-building
(``SshLaunchTargetConfig.build_remote_exec_args``) and sentinel/JSON parsing,
with ``subprocess.run`` faked to dispatch the real vendored script
(``transcript_fs_remote_script``) in-process against ``tmp_path`` standing in
for the remote host. Mirrors test_transcripts.py's symlink_shared cases and
copy_thread_on_switch path, but through the remote seam, asserting the
sequence of emitted remote commands and fail-before-destroy ordering.
"""

import contextlib
import io
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.backends import transcript_fs_remote as remote_mod
from waypoint.backends import transcript_fs_remote_script as script_mod
from waypoint.backends.base import BackendPlugin
from waypoint.backends.bootstrap import build_default_registry
from waypoint.backends.transcript_fs_remote import RemoteTranscriptFilesystem
from waypoint.backends.transcripts import (
    TranscriptUnavailableError,
    ensure_symlink_shared,
    ensure_thread_available,
)
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus

TID = "11111111-1111-1111-1111-111111111111"


def _launch_target() -> SshLaunchTargetConfig:
    return SshLaunchTargetConfig(
        id="remote-1",
        name="Remote",
        ssh_destination="test-host",
        ssh_bin="/bin/sh",
    )


def _plugin(backend: str) -> BackendPlugin:
    return build_default_registry().get(backend)


def _session(backend: str) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id="s1",
        backend=backend,
        source=SessionSource.MANAGED,
        title="t",
        cwd="/repo/app",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/r",
        structured_log_path="/e",
        transport_state={"thread_id": TID},
    )


def _write_codex_rollout(config_dir: Path) -> Path:
    day = config_dir / "sessions" / "2026" / "07" / "08"
    day.mkdir(parents=True)
    path = day / f"rollout-2026-07-08T00-00-00-{TID}.jsonl"
    path.write_text("{}")
    return path


def _dispatch_script(op: str, args: tuple[str, ...]) -> bytes:
    """Run the real vendored script's op dispatch in-process, capturing stdout."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        handler = script_mod.OPS.get(op)
        if handler is None:
            script_mod.emit({"error": "unknown op: " + op})
        else:
            try:
                handler(*args)
            except Exception as exc:  # mirror the script's top-level catch-all
                script_mod.emit({"error": "internal: " + repr(exc)})
    return buf.getvalue().encode("utf-8")


class _FakeRemote:
    """Captures every (op, args) RemoteTranscriptFilesystem issues.

    Answers each by running the real vendored script's dispatch against the
    real local filesystem (``tmp_path`` in tests stands in for the remote
    root) — no SSH process, no real network, but genuine script logic and
    genuine argv-building/sentinel-parsing on the ``RemoteTranscriptFilesystem``
    side. ``force_error`` lets a test simulate a remote-side failure for one
    call without touching the filesystem, to test fail-before-destroy.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self._force_errors: dict[str, str] = {}
        orig_build = SshLaunchTargetConfig.build_remote_exec_args

        def _capturing_build(
            target_self: SshLaunchTargetConfig,
            command: list[str],
            *args: object,
            **kwargs: object,
        ) -> tuple[str, ...]:
            op, op_args = command[2], tuple(command[3:])
            self.calls.append((op, op_args))
            return orig_build(target_self, command, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(
            SshLaunchTargetConfig, "build_remote_exec_args", _capturing_build
        )

        def _fake_run(
            argv: tuple[str, ...],
            *,
            input: bytes | None = None,
            capture_output: bool | None = None,
            timeout: float | None = None,
        ) -> subprocess.CompletedProcess[bytes]:
            op, op_args = self.calls[-1]
            if op in self._force_errors:
                message = self._force_errors.pop(op)
                stdout = (
                    remote_mod.SENTINEL + json.dumps({"error": message}) + "\n"
                ).encode("utf-8")
            else:
                stdout = _dispatch_script(op, op_args)
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")

        monkeypatch.setattr(remote_mod.subprocess, "run", _fake_run)

    def force_error(self, op: str, message: str) -> None:
        self._force_errors[op] = message


@pytest.fixture
def fake_remote(monkeypatch: pytest.MonkeyPatch) -> _FakeRemote:
    return _FakeRemote(monkeypatch)


# ── symlink_shared cases (mirrors test_transcripts.py, through the remote FS)


def test_remote_symlink_shared_creates_symlink_when_missing(
    tmp_path: Path, fake_remote: _FakeRemote
) -> None:
    store = tmp_path / "target" / "projects"
    shared = tmp_path / "shared"
    fs = RemoteTranscriptFilesystem(_launch_target())
    ensure_symlink_shared(store, shared, fs=fs)
    assert store.is_symlink()
    assert store.resolve() == shared.resolve()
    assert ("mkdir", (str(shared), "1", "1")) in fake_remote.calls
    assert ("symlink", (str(store), str(shared))) in fake_remote.calls


def test_remote_symlink_shared_idempotent_on_correct_symlink(
    tmp_path: Path, fake_remote: _FakeRemote
) -> None:
    store = tmp_path / "target" / "projects"
    shared = tmp_path / "shared"
    fs = RemoteTranscriptFilesystem(_launch_target())
    ensure_symlink_shared(store, shared, fs=fs)
    fake_remote.calls.clear()
    ensure_symlink_shared(store, shared, fs=fs)  # no error on second run
    assert store.resolve() == shared.resolve()
    assert ("symlink", (str(store), str(shared))) not in fake_remote.calls


def test_remote_symlink_shared_rejects_wrong_symlink(
    tmp_path: Path, fake_remote: _FakeRemote
) -> None:
    store = tmp_path / "target" / "projects"
    store.parent.mkdir(parents=True)
    store.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    fs = RemoteTranscriptFilesystem(_launch_target())
    with pytest.raises(TranscriptUnavailableError, match="not the configured"):
        ensure_symlink_shared(store, tmp_path / "shared", fs=fs)


def test_remote_symlink_shared_replaces_empty_real_dir(
    tmp_path: Path, fake_remote: _FakeRemote
) -> None:
    store = tmp_path / "target" / "projects"
    store.mkdir(parents=True)
    fs = RemoteTranscriptFilesystem(_launch_target())
    ensure_symlink_shared(store, tmp_path / "shared", fs=fs)
    assert store.is_symlink()
    assert ("rmdir", (str(store),)) in fake_remote.calls


def test_remote_symlink_shared_refuses_nonempty_real_dir(
    tmp_path: Path, fake_remote: _FakeRemote
) -> None:
    store = tmp_path / "target" / "projects"
    store.mkdir(parents=True)
    (store / "keep.jsonl").write_text("{}")
    fs = RemoteTranscriptFilesystem(_launch_target())
    with pytest.raises(TranscriptUnavailableError, match="non-empty"):
        ensure_symlink_shared(store, tmp_path / "shared", fs=fs)
    # Fail-before-destroy: refused before any destructive rmdir/symlink call.
    op_names = [name for name, _ in fake_remote.calls]
    assert "rmdir" not in op_names
    assert "symlink" not in op_names


def test_remote_symlink_shared_end_to_end_makes_thread_visible(
    tmp_path: Path, fake_remote: _FakeRemote
) -> None:
    shared = tmp_path / "shared"
    (shared / "-repo-app").mkdir(parents=True)
    (shared / "-repo-app" / f"{TID}.jsonl").write_text("{}")
    target = tmp_path / "target"
    fs = RemoteTranscriptFilesystem(_launch_target())
    ensure_thread_available(
        _plugin("claude_code"),
        _session("claude_code"),
        current_config_dir=str(tmp_path / "current"),
        target_config_dir=str(target),
        policy="symlink_shared",
        shared_transcript_dir=str(shared),
        native_thread_store="projects",
        fs=fs,
    )
    assert (target / "projects").is_symlink()
    op_names = [name for name, _ in fake_remote.calls]
    assert "glob" in op_names  # discovery + re-check both ran remotely


# ── copy_thread_on_switch (remote) ──────────────────────────────────────────


def test_remote_copy_thread_on_switch_copies_codex_rollout(
    tmp_path: Path, fake_remote: _FakeRemote
) -> None:
    current = tmp_path / "current"
    target = tmp_path / "target"
    src = _write_codex_rollout(current)
    fs = RemoteTranscriptFilesystem(_launch_target())
    ensure_thread_available(
        _plugin("codex"),
        _session("codex"),
        current_config_dir=str(current),
        target_config_dir=str(target),
        policy="copy_thread_on_switch",
        shared_transcript_dir=None,
        native_thread_store="sessions",
        fs=fs,
    )
    dest = target / src.relative_to(current)
    assert dest.is_file()
    assert oct(dest.stat().st_mode)[-3:] == "600"
    op_names = [name for name, _ in fake_remote.calls]
    assert "copy_file" in op_names
    assert "mkdir" in op_names
    assert "glob" in op_names


def test_remote_copy_thread_on_switch_rejects_when_source_missing(
    tmp_path: Path, fake_remote: _FakeRemote
) -> None:
    fs = RemoteTranscriptFilesystem(_launch_target())
    with pytest.raises(TranscriptUnavailableError, match="not found to copy"):
        ensure_thread_available(
            _plugin("codex"),
            _session("codex"),
            current_config_dir=str(tmp_path / "current"),
            target_config_dir=str(tmp_path / "target"),
            policy="copy_thread_on_switch",
            shared_transcript_dir=None,
            native_thread_store="sessions",
            fs=fs,
        )
    # Fail-before-destroy: no copy/mkdir attempted when the source can't be found.
    op_names = [name for name, _ in fake_remote.calls]
    assert "copy_file" not in op_names
    assert "mkdir" not in op_names


def test_remote_copy_thread_on_switch_stops_before_copy_when_mkdir_fails(
    tmp_path: Path, fake_remote: _FakeRemote
) -> None:
    current = tmp_path / "current"
    target = tmp_path / "target"
    _write_codex_rollout(current)
    fs = RemoteTranscriptFilesystem(_launch_target())
    fake_remote.force_error("mkdir", "permission denied")
    with pytest.raises(TranscriptUnavailableError, match="permission denied"):
        ensure_thread_available(
            _plugin("codex"),
            _session("codex"),
            current_config_dir=str(current),
            target_config_dir=str(target),
            policy="copy_thread_on_switch",
            shared_transcript_dir=None,
            native_thread_store="sessions",
            fs=fs,
        )
    # Fail-before-destroy: the failed mkdir stops the copy before it starts,
    # and nothing was written under the target.
    op_names = [name for name, _ in fake_remote.calls]
    assert "copy_file" not in op_names
    assert not target.exists()


# ── transport-failure degradation ───────────────────────────────────────────


def test_remote_glob_artifacts_degrades_to_empty_on_transport_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A read-only query op failing at the transport layer (not the remote
    # logic) must degrade to "not found", not raise — fail-before-destroy
    # relies on this feeding into the policy's own TranscriptUnavailableError
    # rather than crashing with an unrelated exception.
    monkeypatch.setattr(
        SshLaunchTargetConfig,
        "build_remote_exec_args",
        lambda self, command, *a, **kw: ("ssh", "test-host", "python3 -"),
    )
    monkeypatch.setattr(
        remote_mod.subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired("ssh", 20)),
    )
    fs = RemoteTranscriptFilesystem(_launch_target())
    with pytest.raises(TranscriptUnavailableError, match="require_existing"):
        ensure_thread_available(
            _plugin("claude_code"),
            _session("claude_code"),
            current_config_dir=str(tmp_path / "current"),
            target_config_dir=str(tmp_path / "target"),
            policy="require_existing",
            shared_transcript_dir=None,
            native_thread_store="projects",
            fs=fs,
        )


def test_remote_expanduser_leaves_tilde_intact() -> None:
    # The remote fs must NOT expand ``~`` against the backend host — the remote
    # helper script expands it against the remote home per op instead.
    fs = RemoteTranscriptFilesystem(_launch_target())
    assert fs.expanduser("~/.codex-work") == "~/.codex-work"
    assert fs.expanduser("~alice/x") == "~alice/x"
    assert fs.expanduser("/abs/path") == "/abs/path"


def test_remote_script_ops_expand_tilde_against_the_running_host() -> None:
    # Ops resolve ``~`` on the host that runs the script (the remote one in
    # production; here the test host stands in), so a ``~``-relative config_dir
    # reaches the right directory.
    import json
    import os

    payload = _dispatch_script("exists", (os.path.expanduser("~"),))
    line = payload.decode().split(script_mod.SENTINEL, 1)[1]
    assert json.loads(line) == {"exists": True}
    # A bare ``~`` must resolve the same way, not stat a literal "~" entry.
    payload_tilde = _dispatch_script("exists", ("~",))
    line_tilde = payload_tilde.decode().split(script_mod.SENTINEL, 1)[1]
    assert json.loads(line_tilde) == {"exists": True}
