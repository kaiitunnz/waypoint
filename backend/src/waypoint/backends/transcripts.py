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
from datetime import UTC, datetime
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


def _pin_tree_perms(root: Path) -> None:
    """Lock a copied transcript tree to 0700 dirs / 0600 files."""
    root.chmod(0o700)
    for path in root.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)


def setup_transcripts_symlink(store_dir: Path, shared_dir: Path) -> list[str]:
    """Make ``store_dir`` a symlink to ``shared_dir``, migrating existing content.

    The action counterpart to :func:`ensure_symlink_shared`'s guard: it performs
    the one case the guard refuses — a populated real store dir — by migrating
    its contents into the shared dir before replacing it with the symlink. The
    other five cases delegate to :func:`ensure_symlink_shared`. Returns a list of
    the actions taken (for reporting); raises :class:`TranscriptUnavailableError`
    on a same-named conflict or a symlink pointing elsewhere.

    For the populated case the sequence is data-safe against loss: (1) a conflict
    pre-flight refuses before touching anything if any top-level entry already
    exists under ``shared``; (2) contents are copied into a temp sibling of
    ``shared`` (removed afterward) and then renamed into place, so a mid-copy
    failure leaves both ``store`` and ``shared`` intact; (3) the original
    ``store`` is renamed to a timestamped backup (a complete snapshot, not an
    emptied husk); (4) ``store`` becomes the symlink. The original ``store`` is
    never touched until every entry is safely in ``shared``, so no transcript is
    lost. The per-entry rename in step 2 is not a single atomic commit, so a
    process death partway through can leave some entries already under ``shared``;
    a re-run then reports those as conflicts and the operator finishes the move by
    hand — the original data is still intact in ``store``.
    """
    shared_dir = shared_dir.expanduser()
    if store_dir.is_symlink() and store_dir.resolve() == shared_dir.resolve():
        return [f"{store_dir} already links to {shared_dir}"]
    if not store_dir.is_symlink() and store_dir.is_dir() and any(store_dir.iterdir()):
        return _migrate_populated_store(store_dir, shared_dir)
    ensure_symlink_shared(store_dir, shared_dir)
    return [f"linked {store_dir} -> {shared_dir}"]


def _migrate_populated_store(store_dir: Path, shared_dir: Path) -> list[str]:
    shared_dir.mkdir(parents=True, exist_ok=True)
    shared_dir.chmod(0o700)

    entries = sorted(store_dir.iterdir(), key=lambda p: p.name)
    conflicts = [e.name for e in entries if (shared_dir / e.name).exists()]
    if conflicts:
        raise TranscriptUnavailableError(
            f"cannot migrate {store_dir} into {shared_dir}: these entries already "
            f"exist in the shared dir: {', '.join(sorted(conflicts))}"
        )

    # Copy into a temp sibling first, then rename each entry into place, so a
    # failed copy never leaves partial entries under the shared dir. The staging
    # dir is removed on any failure so a re-run isn't blocked by an orphan.
    staging = shared_dir.parent / f".wp-migrate-{_timestamp()}"
    if staging.exists():
        raise TranscriptUnavailableError(f"migration staging dir {staging} exists")
    try:
        shutil.copytree(store_dir, staging, symlinks=True)
        _pin_tree_perms(staging)
        for entry in sorted(staging.iterdir(), key=lambda p: p.name):
            entry.rename(shared_dir / entry.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    backup = store_dir.parent / f"{store_dir.name}.bak-{_timestamp()}"
    store_dir.rename(backup)
    store_dir.symlink_to(shared_dir, target_is_directory=True)
    return [
        f"migrated {len(entries)} entries from {store_dir} into {shared_dir}",
        f"backed up the original dir to {backup}",
        f"linked {store_dir} -> {shared_dir}",
    ]


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


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
        # Lock only the dirs this copy creates to 0700; leave pre-existing ones
        # (including the config root) untouched. The transcript is copied with
        # its metadata (shutil.copy2) then pinned to 0600 as a backstop.
        new_dirs = [
            dst_root / parent
            for parent in reversed(rel.parents)
            if (dst_root / parent) != dst_root and not (dst_root / parent).exists()
        ]
        dest.parent.mkdir(parents=True, exist_ok=True)
        for created in new_dirs:
            created.chmod(0o700)
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
