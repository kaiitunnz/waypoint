"""Native transcript availability for account-profile switching.

Before a config-dir profile switch can resume, the target profile's state root
must be able to see the session's native thread transcript. This applies the
profile's ``transcript_policy`` locally:

- ``require_existing`` — verify the target already has it; never mutate files.
- ``symlink_shared`` — point the target's native store dir at a shared dir.
- ``copy_thread_on_switch`` — copy only the current thread's artifact set.

The runtime drives the locate → check → apply → re-check sequence via
:func:`ensure_thread_available`; per-backend path logic stays in each plugin's
``native_thread_artifacts``. Local launch targets only — remote (over SSH) is a
later phase. Raises :class:`TranscriptUnavailableError`, which the runtime maps
to a 400 (nothing is terminated when it fires).
"""

import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from waypoint.backends.plugin_config import TranscriptPolicy
from waypoint.schemas import SessionRecord

if TYPE_CHECKING:
    from waypoint.backends.base import BackendPlugin

log = logging.getLogger("waypoint.backends.transcripts")


class TranscriptUnavailableError(Exception):
    """The target profile cannot see the native thread and policy can't fix it."""


def ensure_symlink_shared(store_dir: Path, shared_dir: Path) -> None:
    """Ensure ``store_dir`` is a symlink to ``shared_dir`` (guarded).

    Idempotent and non-destructive: a real, populated store dir is refused
    rather than silently converted (migrate it with ``accounts
    setup-transcripts`` first). Cases: missing → create; already the right
    symlink → no-op; a symlink elsewhere → error; an empty real dir → replace;
    a non-empty real dir → error.
    """
    shared_dir.mkdir(parents=True, exist_ok=True)
    shared_dir.chmod(0o700)
    if store_dir.is_symlink():
        if store_dir.resolve() == shared_dir.resolve():
            return
        raise TranscriptUnavailableError(
            f"{store_dir} is a symlink to {os.readlink(store_dir)!r}, "
            f"not the configured shared_transcript_dir {shared_dir}"
        )
    if store_dir.exists():
        if not store_dir.is_dir():
            raise TranscriptUnavailableError(
                f"{store_dir} exists and is not a directory"
            )
        if any(store_dir.iterdir()):
            raise TranscriptUnavailableError(
                f"{store_dir} is a non-empty directory; run "
                "'waypoint accounts setup-transcripts' to migrate it into the "
                "shared dir before switching"
            )
        store_dir.rmdir()
    store_dir.parent.mkdir(parents=True, exist_ok=True)
    store_dir.symlink_to(shared_dir, target_is_directory=True)


def copy_thread_artifacts(
    artifacts: list[Path], src_root: Path, dst_root: Path
) -> None:
    """Copy each artifact from under ``src_root`` to the same relative path
    under ``dst_root``, preserving restrictive permissions.

    Only the passed artifact set is copied — never whole history directories.
    """
    for artifact in artifacts:
        rel = artifact.relative_to(src_root)
        dest = dst_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Keep created dirs owner-only; the transcript itself is copied with its
        # metadata (shutil.copy2) and then pinned to 0600 as a backstop.
        for parent in reversed(dest.relative_to(dst_root).parents):
            (dst_root / parent).chmod(0o700)
        shutil.copy2(artifact, dest)
        dest.chmod(0o600)


def ensure_thread_available(
    plugin: "BackendPlugin",
    session: SessionRecord,
    *,
    current_config_dir: str,
    target_config_dir: str,
    policy: TranscriptPolicy,
    shared_transcript_dir: str | None,
    native_thread_store: str | None,
) -> None:
    """Make the session's native thread visible under ``target_config_dir``.

    No-op when it's already visible. Otherwise applies ``policy`` and re-checks;
    raises :class:`TranscriptUnavailableError` (before any process is touched)
    when the thread still isn't available.
    """
    target = str(Path(target_config_dir).expanduser())
    if plugin.native_thread_artifacts(session, target):
        return

    if policy == "require_existing":
        raise TranscriptUnavailableError(
            "the target account profile cannot see the native thread transcript "
            "and transcript_policy is 'require_existing'"
        )
    if policy == "symlink_shared":
        if not shared_transcript_dir:
            raise TranscriptUnavailableError(
                "transcript_policy 'symlink_shared' requires shared_transcript_dir"
            )
        if not native_thread_store:
            raise TranscriptUnavailableError(
                "backend has no native transcript store for symlink_shared"
            )
        ensure_symlink_shared(
            Path(target) / native_thread_store,
            Path(shared_transcript_dir).expanduser(),
        )
    elif policy == "copy_thread_on_switch":
        current = str(Path(current_config_dir).expanduser())
        artifacts = plugin.native_thread_artifacts(session, current)
        if not artifacts:
            raise TranscriptUnavailableError(
                "source native thread artifact not found to copy"
            )
        copy_thread_artifacts(artifacts, Path(current), Path(target))
    else:  # pragma: no cover - exhaustive over TranscriptPolicy
        raise TranscriptUnavailableError(f"unknown transcript policy {policy!r}")

    if not plugin.native_thread_artifacts(session, target):
        raise TranscriptUnavailableError(
            "native thread still unavailable in the target profile after "
            f"applying transcript_policy {policy!r}"
        )
