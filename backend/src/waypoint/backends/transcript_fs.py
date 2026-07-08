"""IO seam for native-thread transcript-availability policy.

``backends/transcripts.py`` implements the ``require_existing`` /
``symlink_shared`` / ``copy_thread_on_switch`` policy logic against this
minimal filesystem interface, so the same policy code runs unchanged whether
the target account profile is local or (a later phase) reached over SSH —
only the :class:`TranscriptFilesystem` implementation differs, never the
policy branching.
"""

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from waypoint.schemas import SessionRecord

if TYPE_CHECKING:
    from waypoint.backends.base import BackendPlugin


class TranscriptFilesystem(Protocol):
    """Filesystem primitives the transcript policies need, local or remote."""

    def exists(self, path: str) -> bool:
        """Whether ``path`` exists (following symlinks)."""
        ...

    def is_dir(self, path: str) -> bool:
        """Whether ``path`` exists and is a directory."""
        ...

    def is_symlink(self, path: str) -> bool:
        """Whether ``path`` is a symlink (whatever it points at)."""
        ...

    def readlink(self, path: str) -> str:
        """The raw target ``path`` (a symlink) points at, unresolved."""
        ...

    def listdir(self, path: str) -> list[str]:
        """Entry names directly under directory ``path``."""
        ...

    def mkdir(
        self, path: str, *, parents: bool = False, exist_ok: bool = False
    ) -> None:
        """Create directory ``path`` (mirrors ``Path.mkdir``)."""
        ...

    def chmod(self, path: str, mode: int) -> None:
        """Set ``path``'s permission bits to ``mode``."""
        ...

    def rmdir(self, path: str) -> None:
        """Remove the empty directory at ``path``."""
        ...

    def symlink(self, path: str, target: str) -> None:
        """Create a symlink at ``path`` pointing to ``target``."""
        ...

    def copy_file(self, src: str, dst: str, mode: int) -> None:
        """Copy the file ``src`` to ``dst`` and set ``dst``'s mode."""
        ...

    def expanduser(self, path: str) -> str:
        """Expand a leading ``~`` against the home of the filesystem the path
        lives on.

        Local expands against this host; the remote implementation leaves the
        path untouched so the remote shell/interpreter expands it against the
        *remote* home — expanding a remote path against the backend host's home
        would glob the wrong directory.
        """
        ...

    def glob_artifacts(
        self, session: SessionRecord, plugin: "BackendPlugin", config_dir: str
    ) -> list[str]:
        """Discover ``session``'s native thread artifact(s) under ``config_dir``.

        Runs ``plugin.native_thread_artifact_glob(session)`` against
        ``config_dir`` and returns the full matched path(s) (not truncated to
        a bare name) — the caller needs the path relative to ``config_dir`` to
        build a copy destination. Returns ``[]`` when the plugin has no
        pattern for this session (no native store, or no thread id yet) or
        nothing under ``config_dir`` matches.
        """
        ...


class LocalTranscriptFilesystem:
    """Today's pathlib/shutil behavior, exposed through the seam."""

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def is_dir(self, path: str) -> bool:
        return Path(path).is_dir()

    def is_symlink(self, path: str) -> bool:
        return Path(path).is_symlink()

    def readlink(self, path: str) -> str:
        return str(Path(path).readlink())

    def listdir(self, path: str) -> list[str]:
        return [entry.name for entry in Path(path).iterdir()]

    def mkdir(
        self, path: str, *, parents: bool = False, exist_ok: bool = False
    ) -> None:
        Path(path).mkdir(parents=parents, exist_ok=exist_ok)

    def chmod(self, path: str, mode: int) -> None:
        Path(path).chmod(mode)

    def rmdir(self, path: str) -> None:
        Path(path).rmdir()

    def symlink(self, path: str, target: str) -> None:
        Path(path).symlink_to(target, target_is_directory=True)

    def copy_file(self, src: str, dst: str, mode: int) -> None:
        shutil.copy2(src, dst)
        Path(dst).chmod(mode)

    def expanduser(self, path: str) -> str:
        return os.path.expanduser(path)

    def glob_artifacts(
        self, session: SessionRecord, plugin: "BackendPlugin", config_dir: str
    ) -> list[str]:
        pattern = plugin.native_thread_artifact_glob(session)
        if pattern is None:
            return []
        root = Path(config_dir).expanduser()
        if not root.is_dir():
            return []
        return sorted(str(match) for match in root.glob(pattern))
