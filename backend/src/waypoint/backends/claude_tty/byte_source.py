"""Transcript byte sources for the claude_tty tailer.

A byte source abstracts *where* a Claude JSONL transcript lives so the tailer's
cursor / partial-record logic stays storage-independent. A local session reads
the file directly off the shared ``~/.claude/projects`` tree; a session on an
SSH launch target reads it over the existing ``RemoteTranscriptFilesystem``
seam — discovering the file by the Claude thread-artifact glob (never by
encoding a possibly non-canonical remote cwd) and reading bounded byte ranges.

Claude renames a transcript into another project folder when the session's
working folder changes (a worktree, ``/cd``). When the file at the last known
path is missing or replaced, a source finds it again by thread id. A moved file
keeps its identity, so the tailer's offset stays valid.

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
import os
import posixpath
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from waypoint.backends.claude_code.session_folder import launch_folder
from waypoint.backends.claude_code.threads import (
    UUID_RE,
    claude_projects_root,
    encode_project_dir,
    local_claude_thread_artifacts,
)
from waypoint.backends.transcript_fs_remote import RemoteTranscriptFilesystem
from waypoint.backends.transcript_fs_remote_script import find_clear_successors
from waypoint.launch_targets import SshLaunchTargetConfig

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_tty")

# Seconds between remote reads at steady state, and the first search backoff.
_STEADY_INTERVAL = 1.0
# Cap on the exponential backoff of remote errors and local searches.
_MAX_BACKOFF = 10.0
READ_LIMIT = 256 * 1024  # max bytes fetched per remote read


def _single(ids: list[str]) -> str | None:
    # Several successors of one thread are ambiguous; follow none.
    return ids[0] if len(ids) == 1 else None


def _pick_moved[T](
    candidates: list[T],
    identity: tuple[int, int] | None,
    identity_of: Callable[[T], tuple[int, int] | None],
) -> T | None:
    """The candidate holding the transcript last read: the only one there is,
    else the one with its file identity."""
    if len(candidates) == 1:
        return candidates[0]
    if identity is None:
        return None
    return next((c for c in candidates if identity_of(c) == identity), None)


def _stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:
        return None


def _stat_identity(st: os.stat_result) -> tuple[int, int]:
    return (st.st_dev, st.st_ino)


def _path_identity(path: Path) -> tuple[int, int] | None:
    st = _stat(path)
    return None if st is None else _stat_identity(st)


def resolve_remote_config_root(
    config_dir: str | None, cwd: str, fs: RemoteTranscriptFilesystem
) -> str:
    """Resolve the remote ``CLAUDE_CONFIG_DIR`` root for a session.

    ``None`` defaults to ``~/.claude``; an absolute or ``~``-anchored value is
    used verbatim; a relative value resolves against the session's remote cwd
    (not Waypoint's cwd or the SSH login dir).
    """
    if config_dir is None:
        return "~/.claude"
    if config_dir.startswith("/") or config_dir.startswith("~"):
        return config_dir
    base = fs.expanduser(cwd) if cwd.startswith("~") else cwd
    return posixpath.join(base, config_dir)


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
        (start-at-end priming). ``force`` bypasses a source's poll cadence or
        search backoff.
        """
        ...


class ClaudeTranscriptSource(TranscriptByteSource, Protocol):
    def find_clear_successor(
        self, parent_thread_id: str, max_age_seconds: float
    ) -> str | None:
        """The thread id of the transcript a ``/clear`` on ``parent_thread_id``
        started, if one was written within ``max_age_seconds``. Never raises."""
        ...


class LocalTranscriptByteSource:
    """Reads a transcript straight off the local filesystem."""

    def __init__(self, cwd: str, thread_id: str, config_dir: str | None) -> None:
        self._path = transcript_path(cwd, thread_id, config_dir)
        self._thread_id = thread_id
        self._config_dir = config_dir
        self._identity: tuple[int, int] | None = None  # of the file last read
        # Monotonic time before which a miss does not search again.
        self._next_search_at = 0.0
        # Backoff after an empty search, which scans every project folder.
        self._search_delay = 0.0
        self._warned_ambiguous = False

    def read_from(
        self, offset: int, *, metadata_only: bool = False, force: bool = False
    ) -> TranscriptRead:
        st = self._locate(force)
        if st is None:
            # Missing before the first turn (or a transient stat error): keep
            # polling without disturbing the cursor.
            return TranscriptRead(observed=False)
        if metadata_only:
            self._identity = _stat_identity(st)
            return TranscriptRead(
                observed=True, size=st.st_size, identity=self._identity
            )
        try:
            with self._path.open("rb") as fh:
                opened = os.fstat(fh.fileno())
                if (identity := _stat_identity(opened)) != _stat_identity(st):
                    # Renamed and reused since the stat: locate it again.
                    return TranscriptRead(observed=False)
                fh.seek(offset)
                data = fh.read()
        except OSError:
            return TranscriptRead(observed=False)
        self._identity = identity
        return TranscriptRead(
            observed=True, data=data, size=opened.st_size, identity=identity
        )

    def _locate(self, force: bool) -> os.stat_result | None:
        """Stat the transcript, searching for it when the known path lost it.

        A different file at that path is returned when the transcript last read
        is nowhere else, for the tailer to treat as a replacement.
        """
        st = _stat(self._path)
        replaced = st is not None and self._identity not in (None, _stat_identity(st))
        if st is not None and not replaced:
            self._next_search_at = self._search_delay = 0.0
            return st
        if not (replaced or force or time.monotonic() >= self._next_search_at):
            return None
        try:
            candidates = local_claude_thread_artifacts(
                self._thread_id, self._config_dir
            )
        except OSError:
            candidates = []
        moved = _pick_moved(candidates, self._identity, _path_identity)
        if moved is None:
            if len(candidates) > 1 and not self._warned_ambiguous:
                self._warned_ambiguous = True
                log.warning(
                    "multiple transcripts matched one thread",
                    extra={"thread_id": self._thread_id},
                )
            self._search_delay = min(
                max(self._search_delay * 2, _STEADY_INTERVAL), _MAX_BACKOFF
            )
            self._next_search_at = time.monotonic() + self._search_delay
            return st
        self._path = moved
        return _stat(moved)

    def find_clear_successor(
        self, parent_thread_id: str, max_age_seconds: float
    ) -> str | None:
        # Every project folder: the session may have changed folder since the
        # parent transcript's path was derived.
        return _single(
            find_clear_successors(
                str(claude_projects_root(self._config_dir)),
                "*/*.jsonl",
                parent_thread_id,
                max_age_seconds,
            )
        )


class RemoteClaudeTranscriptByteSource:
    """Reads a remote Claude transcript over the SSH filesystem seam.

    Discovers the transcript by the Claude thread-artifact glob
    (``projects/*/<uuid>.jsonl``) under the session's config-dir root, then
    services each poll with a bounded ``read_range``, globbing again only when
    the read fails or the file at the path changes. Throttles to a
    steady-state cadence and backs off exponentially on error; the tailer loop
    keeps ticking for its own pane/dialog work while this source no-ops between
    reads, so SSH stays to roughly one request per second per active tailer.
    """

    def __init__(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        thread_id: str,
        launch_target: SshLaunchTargetConfig,
        config_dir: str | None,
    ) -> None:
        self._runtime = runtime
        self._session_id = session_id
        self._thread_id = thread_id
        self._fs = RemoteTranscriptFilesystem(launch_target)
        self._launch_target_id = launch_target.id
        self._config_dir = config_dir
        self._path: str | None = None
        self._identity: tuple[int, int] | None = None
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

    def find_clear_successor(
        self, parent_thread_id: str, max_age_seconds: float
    ) -> str | None:
        # A ``/clear`` before the first turn names a parent never written, so
        # there is no parent file to find the project dir from: scan them all.
        try:
            root = self._resolved_config_root()
        except Exception:
            self._warn_once("clear-lookup", "remote /clear successor lookup failed")
            return None
        if root is None:
            return None
        return _single(
            self._fs.find_clear_successors(
                root, "projects/*/*.jsonl", parent_thread_id, max_age_seconds
            )
        )

    def _read(self, offset: int, metadata_only: bool) -> TranscriptRead:
        if self._path is None:
            resolved = self._resolve()
            if resolved is None:
                # A clean glob miss is expected until Claude writes after the
                # first turn: keep the steady cadence rather than backing off.
                self._schedule(error=False)
                return TranscriptRead(observed=False)
            self._path = resolved
        limit = 0 if metadata_only else READ_LIMIT
        read = self._fs.read_range(self._path, offset, limit)
        if read is None or self._identity not in (None, read.identity):
            moved = self._resolve()
            if moved is not None and moved != self._path:
                self._path = moved
                read = self._fs.read_range(moved, offset, limit)
        if read is None:
            self._schedule(error=True)
            self._warn_once("read-error", "remote transcript read failed")
            return TranscriptRead(observed=False)
        self._schedule(error=False)
        self._identity = read.identity
        return TranscriptRead(
            observed=True, data=read.data, size=read.size, identity=self._identity
        )

    def _resolve(self) -> str | None:
        if not UUID_RE.match(self._thread_id):
            return None
        if (config_root := self._resolved_config_root()) is None:
            return None
        matches = self._fs.glob(config_root, f"projects/*/{self._thread_id}.jsonl")
        if not matches:
            return None
        found = _pick_moved(matches, self._identity, self._remote_identity)
        if found is None:
            # A UUID is unique under projects/; more than one match is
            # ambiguous — refuse to pick rather than tail an arbitrary file.
            self._warn_once("multi-match", "multiple transcripts matched one thread")
        return found

    def _remote_identity(self, path: str) -> tuple[int, int] | None:
        read = self._fs.read_range(path, 0, 0)
        return None if read is None else read.identity

    def _resolved_config_root(self) -> str | None:
        if self._config_root is None:
            session = self._runtime.storage.get_session(self._session_id)
            if session is None:
                return None
            self._config_root = resolve_remote_config_root(
                self._config_dir, launch_folder(session), self._fs
            )
        return self._config_root

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
