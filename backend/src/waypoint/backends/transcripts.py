"""Native transcript availability for account-profile switching.

Before a config-dir profile switch can resume, the target profile's state root
must be able to see the session's native thread transcript. This applies the
profile's ``transcript_policy``:

- ``require_existing`` — verify the target already has it; never mutate files.
- ``symlink_shared`` — point the target's native store dir at a shared dir.
- ``copy_thread_on_switch`` — copy only the current thread's artifact set.

The runtime drives the locate → check → apply → re-check sequence via
:func:`ensure_thread_available`; per-backend path knowledge stays in each
plugin's ``native_thread_artifact_glob``. All IO goes through the
:class:`~waypoint.backends.transcript_fs.TranscriptFilesystem` seam
(:mod:`waypoint.backends.transcript_fs`), defaulting to
``LocalTranscriptFilesystem`` — a remote implementation (over SSH) plugs into
the same policy code unchanged. Raises :class:`TranscriptUnavailableError`,
which the runtime maps to a 400 (nothing is terminated when it fires).
"""

import filecmp
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from waypoint.backends.plugin_config import TranscriptPolicy
from waypoint.backends.transcript_fs import (
    LocalTranscriptFilesystem,
    TranscriptFilesystem,
)
from waypoint.schemas import SessionRecord

if TYPE_CHECKING:
    from waypoint.backends.base import BackendPlugin

log = logging.getLogger("waypoint.backends.transcripts")

_LOCAL_FS = LocalTranscriptFilesystem()


class TranscriptUnavailableError(Exception):
    """The target profile cannot see the native thread and policy can't fix it."""


class ThreadAvailability(StrEnum):
    """Native-thread state prepared for a profile switch."""

    PERSISTED = "persisted"
    UNPERSISTED = "unpersisted"


def unpersisted_thread_error(policy: TranscriptPolicy) -> TranscriptUnavailableError:
    """Return the legacy availability error for a backend that cannot restart fresh."""
    if policy == "copy_thread_on_switch":
        return TranscriptUnavailableError(
            "source native thread artifact not found to copy"
        )
    return TranscriptUnavailableError(
        "native thread still unavailable in the target profile after "
        f"applying transcript_policy {policy!r}"
    )


def ensure_symlink_shared(
    store_dir: Path,
    shared_dir: Path,
    *,
    fs: TranscriptFilesystem = _LOCAL_FS,
) -> None:
    """Ensure ``store_dir`` is a symlink to ``shared_dir`` (guarded).

    Idempotent and non-destructive: a real, populated store dir is refused
    rather than silently converted (migrate it with ``accounts
    setup-transcripts`` first). Cases: missing → create; already the right
    symlink → no-op; a symlink elsewhere → error; an empty real dir → replace;
    a non-empty real dir → error.
    """
    store, shared = str(store_dir), str(shared_dir)
    fs.mkdir(shared, parents=True, exist_ok=True)
    fs.chmod(shared, 0o700)
    if fs.is_symlink(store):
        target = fs.readlink(store)
        if target == shared:
            return
        raise TranscriptUnavailableError(
            f"{store_dir} is a symlink to {target!r}, "
            f"not the configured shared_transcript_dir {shared_dir}"
        )
    if fs.exists(store):
        if not fs.is_dir(store):
            raise TranscriptUnavailableError(
                f"{store_dir} exists and is not a directory"
            )
        if fs.listdir(store):
            raise TranscriptUnavailableError(
                f"{store_dir} is a non-empty directory; run "
                "'waypoint accounts setup-transcripts' to migrate it into the "
                "shared dir before switching"
            )
        fs.rmdir(store)
    fs.mkdir(str(store_dir.parent), parents=True, exist_ok=True)
    fs.symlink(store, shared)


def setup_transcripts_symlink(store_dir: Path, shared_dir: Path) -> list[str]:
    """Make ``store_dir`` a symlink to ``shared_dir``, migrating existing content.

    The action counterpart to :func:`ensure_symlink_shared`'s guard: it performs
    the one case the guard refuses — a populated real store dir — by migrating
    its contents into the shared dir before replacing it with the symlink. The
    other five cases delegate to :func:`ensure_symlink_shared`. Returns a list of
    the actions taken (for reporting); raises :class:`TranscriptUnavailableError`
    on a genuine leaf conflict or a symlink pointing elsewhere.

    This is the local-only migration CLI (``waypoint accounts
    setup-transcripts``), not part of the account-profile switch flow that
    goes remote — its data-safe migration (staged copy, atomic rename,
    timestamped backup) has no remote counterpart in scope, so it stays on
    ``pathlib``/``os``/``shutil`` directly rather than the
    ``TranscriptFilesystem`` seam.

    For the populated case the merge is *recursive and conflict-aware*: two
    stores may share directory ancestors (the common Codex ``sessions/YYYY/...``
    and Claude ``projects/<project>/...`` layouts) as long as their leaf files do
    not truly collide. A byte-identical leaf at the same relative path is
    deduplicated (the shared copy is retained); only a differing file, a
    file/directory type mismatch, or a diverging symlink target is a conflict.

    The sequence is data-safe against loss: (1) a recursive pre-flight builds the
    full plan and refuses before touching anything if any leaf genuinely
    conflicts; (2) new files/links are copied into a temp staging sibling of
    ``shared`` and verified against their source; (3) each staged entry is
    renamed into ``shared`` (re-checking the destination is still absent first);
    (4) the original ``store`` is renamed to a timestamped backup (a complete
    snapshot, not an emptied husk); (5) ``store`` becomes the symlink. The
    original ``store`` is never touched until every entry is safely in ``shared``,
    so no transcript is lost. The per-entry rename in step 3 is not a single
    atomic commit, so a process death partway through can leave some entries
    already under ``shared``; a re-run then reports only those new destinations as
    conflicts (identical leaves dedup cleanly) and the operator finishes the move
    by hand — the original data is still intact in ``store``.
    """
    shared_dir = shared_dir.expanduser()
    if store_dir.is_symlink() and store_dir.resolve() == shared_dir.resolve():
        return [f"{store_dir} already links to {shared_dir}"]
    if not store_dir.is_symlink() and store_dir.is_dir() and any(store_dir.iterdir()):
        return _migrate_populated_store(store_dir, shared_dir)
    ensure_symlink_shared(store_dir, shared_dir)
    return [f"linked {store_dir} -> {shared_dir}"]


_MAX_REPORTED_CONFLICTS = 10


@dataclass
class _MigrationPlan:
    """A recursive merge plan keyed by relative path (paths only, no contents)."""

    dirs_to_create: list[PurePosixPath] = field(default_factory=list)
    files_to_copy: list[PurePosixPath] = field(default_factory=list)
    symlinks_to_create: list[tuple[PurePosixPath, str]] = field(default_factory=list)
    dedup_count: int = 0
    conflicts: list[tuple[PurePosixPath, str]] = field(default_factory=list)


def _classify_into_plan(
    src: Path, dst: Path, rel: PurePosixPath, plan: _MigrationPlan
) -> None:
    """Classify one source entry against its shared-store counterpart.

    Never follows symlinks (uses ``lstat``/``lexists`` semantics): a symlink is a
    leaf regardless of its target's type, and a broken destination symlink counts
    as present, not absent. Recurses into merged directories.
    """
    dst_absent = not os.path.lexists(dst)
    if src.is_symlink():
        target = os.readlink(src)
        if dst_absent:
            plan.symlinks_to_create.append((rel, target))
        elif dst.is_symlink() and os.readlink(dst) == target:
            plan.dedup_count += 1
        elif dst.is_symlink():
            plan.conflicts.append((rel, "different symlink targets"))
        else:
            kind = "directory" if dst.is_dir() else "file"
            plan.conflicts.append((rel, f"source symlink, shared {kind}"))
        return
    if src.is_dir():
        if dst_absent:
            plan.dirs_to_create.append(rel)
            _walk_into_plan(src, dst, rel, plan)
        elif dst.is_dir() and not dst.is_symlink():
            _walk_into_plan(src, dst, rel, plan)
        else:
            kind = "symlink" if dst.is_symlink() else "file"
            plan.conflicts.append((rel, f"source directory, shared {kind}"))
        return
    if src.is_file():
        if dst_absent:
            plan.files_to_copy.append(rel)
        elif dst.is_symlink():
            plan.conflicts.append((rel, "source file, shared symlink"))
        elif dst.is_dir():
            plan.conflicts.append((rel, "source file, shared directory"))
        elif filecmp.cmp(src, dst, shallow=False):
            plan.dedup_count += 1
        else:
            plan.conflicts.append((rel, "different regular files"))
        return
    plan.conflicts.append((rel, "unsupported entry type"))


def _walk_into_plan(
    src_dir: Path, dst_dir: Path, rel_dir: PurePosixPath, plan: _MigrationPlan
) -> None:
    for entry in sorted(os.scandir(src_dir), key=lambda e: e.name):
        rel = rel_dir / entry.name
        _classify_into_plan(Path(entry.path), dst_dir / entry.name, rel, plan)


def _build_migration_plan(store_dir: Path, shared_dir: Path) -> _MigrationPlan:
    plan = _MigrationPlan()
    _walk_into_plan(store_dir, shared_dir, PurePosixPath(), plan)
    return plan


def _conflict_error(
    store_dir: Path, shared_dir: Path, conflicts: list[tuple[PurePosixPath, str]]
) -> TranscriptUnavailableError:
    ordered = sorted(conflicts)
    lines = [f"{rel} ({reason})" for rel, reason in ordered[:_MAX_REPORTED_CONFLICTS]]
    extra = len(ordered) - len(lines)
    if extra > 0:
        lines.append(f"... and {extra} more")
    detail = "\n".join(lines)
    return TranscriptUnavailableError(
        f"cannot migrate {store_dir} into {shared_dir}: "
        f"{len(ordered)} conflicting paths:\n{detail}"
    )


def _mkdir_pinned(path: Path) -> None:
    """Create ``path`` and any missing ancestors, pinning each level to 0700.

    ``os.makedirs(mode=...)`` applies the mode only to the leaf (and umask-masks
    it), so intermediate levels would land at 0755 — pin each level explicitly.
    """
    if path.exists():
        return
    _mkdir_pinned(path.parent)
    path.mkdir()
    path.chmod(0o700)


def _migrate_populated_store(store_dir: Path, shared_dir: Path) -> list[str]:
    shared_dir.mkdir(parents=True, exist_ok=True)
    shared_dir.chmod(0o700)

    plan = _build_migration_plan(store_dir, shared_dir)
    if plan.conflicts:
        raise _conflict_error(store_dir, shared_dir, plan.conflicts)

    # Stage new files/links in a temp sibling of ``shared`` and verify them
    # against their source before touching ``shared`` — a failure here leaves both
    # stores intact and the staging dir is cleaned up. ``merge_started`` flips once
    # the mutation phase begins: a failure from then on retains the staging dir for
    # recovery diagnosis (``shared`` may be partially populated).
    staging = shared_dir.parent / f".wp-migrate-{_timestamp()}"
    if staging.exists():
        raise TranscriptUnavailableError(f"migration staging dir {staging} exists")
    _mkdir_pinned(staging)
    merge_started = False
    try:
        for rel in plan.dirs_to_create:
            _mkdir_pinned(staging / rel)
        for rel in plan.files_to_copy:
            dest = staging / rel
            _mkdir_pinned(dest.parent)
            shutil.copyfile(store_dir / rel, dest)
            dest.chmod(0o600)
        for rel, target in plan.symlinks_to_create:
            dest = staging / rel
            _mkdir_pinned(dest.parent)
            os.symlink(target, dest)

        for rel in plan.files_to_copy:
            if not filecmp.cmp(store_dir / rel, staging / rel, shallow=False):
                raise TranscriptUnavailableError(
                    f"staged copy of {rel} did not match its source; aborting"
                )

        merge_started = True
        _merge_staged_into_shared(plan, staging, shared_dir)
    except Exception:
        if not merge_started:
            shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(staging, ignore_errors=True)

    backup = store_dir.parent / f"{store_dir.name}.bak-{_timestamp()}"
    store_dir.rename(backup)
    store_dir.symlink_to(shared_dir, target_is_directory=True)

    copied = len(plan.files_to_copy) + len(plan.symlinks_to_create)
    return [
        f"migrated {copied} files ({plan.dedup_count} deduplicated, "
        f"{len(plan.dirs_to_create)} dirs created) from {store_dir} into "
        f"{shared_dir}",
        f"backed up the original dir to {backup}",
        f"linked {store_dir} -> {shared_dir}",
    ]


def _merge_staged_into_shared(
    plan: _MigrationPlan, staging: Path, shared_dir: Path
) -> None:
    """Rename staged entries into ``shared`` in sorted relative-path order.

    Each destination was proven absent during preflight; re-check with ``lexists``
    before every rename to catch a concurrent change and abort loudly rather than
    overwrite.
    """
    for rel in sorted(plan.dirs_to_create):
        _mkdir_pinned(shared_dir / rel)
    moves = plan.files_to_copy + [rel for rel, _ in plan.symlinks_to_create]
    for rel in sorted(moves):
        dest = shared_dir / rel
        if os.path.lexists(dest):
            raise TranscriptUnavailableError(
                f"destination {rel} appeared during migration; aborting before "
                f"overwrite (staged copies are in {staging})"
            )
        (staging / rel).rename(dest)


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def copy_thread_artifacts(
    artifacts: list[str],
    src_root: str,
    dst_root: str,
    *,
    fs: TranscriptFilesystem = _LOCAL_FS,
) -> None:
    """Copy each artifact from under ``src_root`` to the same relative path
    under ``dst_root``, preserving restrictive permissions.

    Only the passed artifact set is copied — never whole history directories.
    """
    src, dst = PurePosixPath(src_root), PurePosixPath(dst_root)
    for artifact in artifacts:
        rel = PurePosixPath(artifact).relative_to(src)
        dest = dst / rel
        # Lock only the dirs this copy creates to 0700; leave pre-existing ones
        # (including the config root) untouched. The transcript is copied with
        # its metadata then pinned to 0600 as a backstop.
        new_dirs = [
            dst / parent
            for parent in reversed(rel.parents)
            if (dst / parent) != dst and not fs.exists(str(dst / parent))
        ]
        fs.mkdir(str(dest.parent), parents=True, exist_ok=True)
        for created in new_dirs:
            fs.chmod(str(created), 0o700)
        fs.copy_file(artifact, str(dest), 0o600)


def ensure_thread_available(
    plugin: "BackendPlugin",
    session: SessionRecord,
    *,
    current_config_dir: str | None,
    target_config_dir: str,
    policy: TranscriptPolicy,
    shared_transcript_dir: str | None,
    native_thread_store: str | None,
    fs: TranscriptFilesystem = _LOCAL_FS,
) -> ThreadAvailability:
    """Make the session's native thread visible under ``target_config_dir``.

    No-op when it's already visible. Otherwise applies ``policy`` and re-checks;
    raises :class:`TranscriptUnavailableError` (before any process is touched)
    when the thread still isn't available. All filesystem access — local by
    default via ``fs`` — goes through the ``TranscriptFilesystem`` seam.
    """
    target = fs.expanduser(target_config_dir)
    if fs.glob_artifacts(session, plugin, target):
        return ThreadAvailability.PERSISTED

    if policy == "require_existing":
        raise TranscriptUnavailableError(
            "the target account profile cannot see the native thread transcript "
            "and transcript_policy is 'require_existing'"
        )
    if not current_config_dir:
        if policy == "copy_thread_on_switch":
            raise TranscriptUnavailableError(
                "cannot determine the current config dir to copy the thread from"
            )
        raise TranscriptUnavailableError(
            "cannot determine the current config dir to verify the native thread"
        )
    current = fs.expanduser(current_config_dir)
    source_artifacts = fs.glob_artifacts(session, plugin, current)
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
            Path(fs.expanduser(shared_transcript_dir)),
            fs=fs,
        )
    elif policy == "copy_thread_on_switch":
        if source_artifacts:
            copy_thread_artifacts(source_artifacts, current, target, fs=fs)
    else:  # pragma: no cover - exhaustive over TranscriptPolicy
        raise TranscriptUnavailableError(f"unknown transcript policy {policy!r}")

    if fs.glob_artifacts(session, plugin, target):
        return ThreadAvailability.PERSISTED
    if source_artifacts == [] and policy in {
        "symlink_shared",
        "copy_thread_on_switch",
    }:
        return ThreadAvailability.UNPERSISTED
    raise unpersisted_thread_error(policy)
