"""The folder a Claude session works in.

Claude can move a session to another folder (a worktree, ``/cd``). The record's
``cwd``, repo and branch follow it, and ``transport_state`` keeps the folder the
session was created in, for when the one it moved to is removed.
"""

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from waypoint.backends.events import mark_important
from waypoint.backends.transcript_fs_remote import RemoteTranscriptFilesystem
from waypoint.git_meta import GitMeta, git_common_dir, resolve_git_meta
from waypoint.schemas import SessionRecord, SessionSource

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_code")

# transport_state key of the folder the session was created in, set once it moves.
_LAUNCH_CWD_KEY = "launch_cwd"


def _recorded_launch_cwd(state: Mapping[str, Any]) -> str | None:
    value = state.get(_LAUNCH_CWD_KEY)
    return value if isinstance(value, str) and value else None


def launch_folder(session: SessionRecord) -> str:
    """The folder the session was created in (a fork keeps its source's)."""
    return _recorded_launch_cwd(session.transport_state) or session.cwd


def carried_launch_cwd(state: Mapping[str, Any]) -> dict[str, str]:
    """``launch_cwd`` to keep when a relaunch rebuilds ``transport_state``."""
    launch_cwd = _recorded_launch_cwd(state)
    return {} if launch_cwd is None else {_LAUNCH_CWD_KEY: launch_cwd}


async def _folder_exists(
    runtime: "SessionRuntime", session: SessionRecord, folder: str
) -> bool | None:
    """Whether ``folder`` is on the session's host; ``None`` if it can't be asked."""
    if session.launch_target_id is None:
        return await asyncio.to_thread(Path(folder).expanduser().is_dir)
    launch_target = runtime._find_launch_target(session.launch_target_id)
    if launch_target is None:
        return None
    fs = RemoteTranscriptFilesystem(launch_target)
    return await asyncio.to_thread(fs.probe_dir, folder)


async def relaunch_folder(runtime: "SessionRuntime", session: SessionRecord) -> str:
    """The folder to run ``session``'s CLI in: its own, or the one it was
    created in when the folder it moved to has been removed."""
    launch = launch_folder(session)
    # Only a definite "missing" counts: a failed SSH probe is not a removal.
    if (
        launch != session.cwd
        and await _folder_exists(runtime, session, session.cwd) is False
        and await _folder_exists(runtime, session, launch)
    ):
        return launch
    return session.cwd


async def _git_meta_for(session: SessionRecord, folder: str) -> GitMeta:
    """Repo and branch of a session moving to ``folder``."""
    if session.launch_target_id is not None:
        # The git state is on the remote host.
        return GitMeta(repo_name=session.repo_name, branch=None)
    meta = await resolve_git_meta(folder)
    if (common := await git_common_dir(folder)) is None:
        return meta
    # A repository keeps the name it had; a new one is named after its main
    # checkout, not a worktree. The folder being left can be gone or in another
    # repository, so the creation folder is compared too.
    if common == await git_common_dir(session.cwd):
        meta.repo_name = session.repo_name
    elif common == await git_common_dir(launch := launch_folder(session)):
        meta.repo_name = (await resolve_git_meta(launch)).repo_name
    elif (git_dir := Path(common)).name == ".git":
        meta.repo_name = git_dir.parent.name
    return meta


async def folder_fields(session: SessionRecord, folder: str) -> dict[str, Any]:
    """The ``cwd``, repo and branch of ``session`` working in ``folder``."""
    if folder == session.cwd:
        meta = GitMeta(repo_name=session.repo_name, branch=session.branch)
    else:
        meta = await _git_meta_for(session, folder)
    return {"cwd": folder, "repo_name": meta.repo_name, "branch": meta.branch}


async def _set_folder(
    runtime: "SessionRuntime", session: SessionRecord, folder: str
) -> SessionRecord:
    """Move the session's record to ``folder``, keeping its launch folder."""
    fields = await folder_fields(session, folder)
    # Re-read: the lookup yielded.
    session = runtime.get_session(session.id)
    state = {**session.transport_state, _LAUNCH_CWD_KEY: launch_folder(session)}
    return await runtime.update_session_fields(
        session.id, **fields, transport_state=state
    )


async def note_removed_folder(
    runtime: "SessionRuntime", session_id: str, source: SessionRecord, folder: str
) -> None:
    """Note in ``session_id`` that ``source``'s folder is gone, when it starts in
    another ``folder``."""
    if folder == source.cwd:
        return
    await runtime._record_system_event(
        session_id,
        f"Folder {source.cwd} no longer exists; starting in {folder}",
        metadata=mark_important({}),
    )


async def leave_removed_folder(
    runtime: "SessionRuntime", session: SessionRecord
) -> SessionRecord:
    """Before a relaunch, move a session whose folder is gone back to the one it
    was created in."""
    folder = await relaunch_folder(runtime, session)
    if folder == session.cwd:
        return session
    moved = await _set_folder(runtime, session, folder)
    await note_removed_folder(runtime, session.id, session, folder)
    return moved


async def follow_relocation(
    runtime: "SessionRuntime", session_id: str, folder: str
) -> None:
    """Record the folder Claude moved the session to. Never raises: an error would
    end the transcript tailer."""
    try:
        session = runtime.storage.get_session(session_id)
        if (
            session is None
            # Boot replaces an assistant whose cwd left its workspace.
            or session.source is SessionSource.ASSISTANT
            or session.cwd == folder
        ):
            return
        if await _folder_exists(runtime, session, folder):
            await _set_folder(runtime, session, folder)
    except Exception:
        log.exception(
            "failed to follow the session's folder", extra={"session_id": session_id}
        )
