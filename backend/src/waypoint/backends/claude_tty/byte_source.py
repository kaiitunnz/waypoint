"""Transcript byte sources for the claude_tty tailer.

A byte source abstracts *where* a Claude JSONL transcript lives so the tailer's
cursor / partial-record logic stays storage-independent. A local session reads
the file directly off the shared ``~/.claude/projects`` tree; a session on an
SSH launch target reads it over the existing ``RemoteTranscriptFilesystem``
seam — discovering the file by the Claude thread-artifact glob (never by
encoding a possibly non-canonical remote cwd) and reading bounded byte ranges.

Every read returns a :class:`TranscriptRead`. ``observed=False`` means "nothing
actionable this tick" — a pre-first-turn missing file, an unresolved remote
glob, a throttled poll, or a transport error backing off; the tailer does
nothing (no offset advance, no discontinuity, no status change). ``observed=
True`` (even with empty ``data``) means the tailer has a valid file observation
and may parse and run its truncation checks. A source **never raises**: a
background tailer loop treats a raised exception as fatal, so all remote failure
modes degrade to an unobserved read that retries with backoff.
"""

import logging
import posixpath
import time
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from waypoint.backends.claude_code.threads import (
    claude_projects_root,
    encode_project_dir,
)
from waypoint.backends.transcript_fs_remote import RemoteTranscriptFilesystem
from waypoint.launch_targets import SshLaunchTargetConfig

if TYPE_CHECKING:
    from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_tty")

_STEADY_INTERVAL = 1.0  # seconds between remote reads at steady state
_MAX_BACKOFF = 10.0  # cap on the exponential error backoff
_READ_LIMIT = 256 * 1024  # max bytes fetched per remote read


def transcript_path(cwd: str, session_uuid: str, config_dir: str | None = None) -> Path:
    """Return the Claude TUI's JSONL transcript path for a local session.

    Resolves the store root and the encoded project-dir name through the same
    helpers thread discovery uses, so the tailed path matches where the CLI
    actually writes — including for cwds with non-slash special characters
    (hidden dirs, worktrees). ``config_dir`` is the session's
    ``CLAUDE_CONFIG_DIR`` (e.g. an account profile's); pass it or the path
    resolves under the default ``~/.claude`` and the tailer reads the wrong
    file for a profile-scoped session — the session then never leaves
    ``running`` because no transcript records reach the normalizer.
    """
    return (
        claude_projects_root(config_dir)
        / encode_project_dir(cwd)
        / f"{session_uuid}.jsonl"
    )


class TranscriptRead:
    """The result of one poll of a transcript byte source.

    ``size`` and ``identity`` (``(device, inode)``) describe the file at read
    time so the tailer can detect a truncation (size drops below its cursor) or
    a replacement (identity changes) without re-reading the whole file.
    """

    __slots__ = ("observed", "data", "size", "identity")

    def __init__(
        self,
        *,
        observed: bool = False,
        data: bytes = b"",
        size: int | None = None,
        identity: tuple[int, int] | None = None,
    ) -> None:
        self.observed = observed
        self.data = data
        self.size = size
        self.identity = identity


class TranscriptByteSource(Protocol):
    def read_from(
        self, offset: int, *, metadata_only: bool = False, force: bool = False
    ) -> TranscriptRead:
        """Read from ``offset``.

        ``metadata_only`` asks for size + identity without fetching the body
        (start-at-end priming). ``force`` bypasses a source's poll-cadence gate
        (used by the tailer's terminal drain on session exit).
        """
        ...


class LocalTranscriptByteSource:
    """Reads a transcript straight off the local filesystem."""

    def __init__(self, cwd: str, thread_id: str, config_dir: str | None) -> None:
        self._path = transcript_path(cwd, thread_id, config_dir)

    def read_from(
        self, offset: int, *, metadata_only: bool = False, force: bool = False
    ) -> TranscriptRead:
        try:
            st = self._path.stat()
        except OSError:
            # Missing before the first turn (or a transient stat error): keep
            # polling without disturbing the cursor. ``force`` is a no-op — a
            # local read is already unthrottled.
            return TranscriptRead(observed=False)
        identity = (st.st_dev, st.st_ino)
        if metadata_only:
            return TranscriptRead(observed=True, size=st.st_size, identity=identity)
        try:
            with self._path.open("rb") as fh:
                fh.seek(offset)
                data = fh.read()
        except OSError:
            return TranscriptRead(observed=False)
        return TranscriptRead(
            observed=True, data=data, size=st.st_size, identity=identity
        )


class RemoteClaudeTranscriptByteSource:
    """Reads a remote Claude transcript over the SSH filesystem seam.

    Discovers the transcript once by the Claude thread-artifact glob
    (``projects/*/<uuid>.jsonl``) under the session's config-dir root, then
    services each poll with a bounded ``read_range``. Throttles to a
    steady-state cadence and backs off exponentially on error; the tailer loop
    keeps ticking for its own pane/dialog work while this source no-ops between
    reads, so SSH stays to roughly one request per second per active tailer.
    """

    def __init__(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        plugin: "ClaudeTtyPlugin",
        launch_target: SshLaunchTargetConfig,
        config_dir: str | None,
    ) -> None:
        self._runtime = runtime
        self._session_id = session_id
        self._plugin = plugin
        self._fs = RemoteTranscriptFilesystem(launch_target)
        self._launch_target_id = launch_target.id
        self._config_dir = config_dir
        self._path: str | None = None
        self._config_root: str | None = None
        self._next_read_at = 0.0  # monotonic deadline; 0 = read immediately
        self._backoff = _STEADY_INTERVAL
        self._warned: set[str] = set()

    def read_from(
        self, offset: int, *, metadata_only: bool = False, force: bool = False
    ) -> TranscriptRead:
        if not force and time.monotonic() < self._next_read_at:
            return TranscriptRead(observed=False)
        try:
            return self._read(offset, metadata_only)
        except Exception:
            # A raised remote/transport error must never kill the tailer loop
            # (RFC req 8). ``expanduser`` on a relative config dir can raise;
            # everything degrades to an unobserved read with error backoff.
            self._schedule(error=True)
            self._warn_once("read-error", "remote transcript read failed")
            return TranscriptRead(observed=False)

    def _read(self, offset: int, metadata_only: bool) -> TranscriptRead:
        if self._path is None:
            resolved = self._resolve()
            if resolved is None:
                # A clean glob miss is expected until Claude writes after the
                # first turn: keep the steady cadence rather than backing off.
                self._schedule(error=False)
                return TranscriptRead(observed=False)
            self._path = resolved
        limit = 0 if metadata_only else _READ_LIMIT
        read = self._fs.read_range(self._path, offset, limit)
        if read is None:
            self._schedule(error=True)
            self._warn_once("read-error", "remote transcript read failed")
            return TranscriptRead(observed=False)
        self._schedule(error=False)
        return TranscriptRead(
            observed=True,
            data=read.data,
            size=read.size,
            identity=(read.device, read.inode),
        )

    def _resolve(self) -> str | None:
        session = self._runtime.storage.get_session(self._session_id)
        if session is None:
            return None
        config_root = self._resolved_config_root(session.cwd)
        matches = self._fs.glob_artifacts(session, self._plugin, config_root)
        if not matches:
            return None
        if len(matches) > 1:
            # A UUID is unique under projects/; more than one match is
            # ambiguous — refuse to pick rather than tail an arbitrary file.
            self._warn_once("multi-match", "multiple transcripts matched one thread")
            return None
        return matches[0]

    def _resolved_config_root(self, cwd: str) -> str:
        if self._config_root is not None:
            return self._config_root
        cfg = self._config_dir
        if cfg is None:
            root = "~/.claude"
        elif cfg.startswith("/") or cfg.startswith("~"):
            root = cfg
        else:
            # A relative CLAUDE_CONFIG_DIR resolves against the session's remote
            # cwd, mirroring the launch's ``cd cwd`` before it inherits the env
            # var — never against Waypoint's cwd or the SSH login dir.
            base = self._fs.expanduser(cwd) if cwd.startswith("~") else cwd
            root = posixpath.join(base, cfg)
        self._config_root = root
        return root

    def _schedule(self, *, error: bool) -> None:
        self._backoff = (
            min(self._backoff * 2, _MAX_BACKOFF) if error else _STEADY_INTERVAL
        )
        self._next_read_at = time.monotonic() + self._backoff

    def _warn_once(self, category: str, message: str) -> None:
        if category in self._warned:
            return
        self._warned.add(category)
        log.warning(
            message,
            extra={
                "session_id": self._session_id,
                "launch_target_id": self._launch_target_id,
                "category": category,
                "retry_delay": round(self._backoff, 1),
            },
        )
