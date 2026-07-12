"""Bounded, metadata-only filesystem footprint walk.

Sums logical regular-file bytes (``st_size``) under a tree without ever reading
file content, following the ``workspace_preview`` walk model. It never follows
symlinks (a symlinked file or directory is skipped, not traversed), dedups
hard-linked inodes against a shared set so one physical inode is counted once
across the canonical total, and stops on an entry-count or wall-clock budget so
a runaway tree degrades to a partial (``truncated``) measurement rather than
stalling collection (PRD NFR Correctness/Performance).
"""

import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# PRD NFR Performance: each filesystem tree gets a 50,000-entry and 2-second
# budget; on breach the category stops and is reported partial.
ENTRY_BUDGET = 50_000
TIME_BUDGET_SECONDS = 2.0

# ``on_file`` receives each counted (post-dedup) regular file and its lstat.
OnFile = Callable[[Path, os.stat_result], None]


@dataclass
class TreeFootprint:
    bytes: int = 0
    file_count: int = 0
    truncated: bool = False

    def merge(self, other: "TreeFootprint") -> None:
        self.bytes += other.bytes
        self.file_count += other.file_count
        self.truncated = self.truncated or other.truncated


class FootprintWalker:
    """Accumulates footprint across the roots of one logical tree.

    One walker per canonical category so the 50k-entry / 2s budget applies to
    that whole tree even when it spans many session directories, while the
    ``seen_inodes`` set is shared across every walker so a hard link spanning
    two categories is counted once (in the category walked first).
    """

    def __init__(
        self,
        seen_inodes: set[tuple[int, int]],
        *,
        entry_budget: int = ENTRY_BUDGET,
        time_budget_s: float = TIME_BUDGET_SECONDS,
    ) -> None:
        self._seen = seen_inodes
        self._remaining = entry_budget
        self._deadline = time.monotonic() + time_budget_s

    def walk(self, root: Path, on_file: OnFile | None = None) -> TreeFootprint:
        fp = TreeFootprint()
        try:
            if root.is_symlink() or not root.exists():
                return fp
        except OSError:
            return fp
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Prune symlinked subdirectories so traversal never follows a link
            # out of the tree.
            dirnames[:] = [
                d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
            ]
            for name in filenames:
                if self._remaining <= 0 or time.monotonic() > self._deadline:
                    fp.truncated = True
                    return fp
                self._remaining -= 1
                path = Path(dirpath) / name
                try:
                    st = os.stat(path, follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                    continue
                if st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    if key in self._seen:
                        continue
                    self._seen.add(key)
                fp.bytes += st.st_size
                fp.file_count += 1
                if on_file is not None:
                    on_file(path, st)
        return fp
