"""Where a session's workspace operations run: in-process or over SSH.

Both implementations execute the same :mod:`waypoint.workspace_preview` ops —
``LocalWorkspaceFilesystem`` calls them directly, while
``RemoteWorkspaceFilesystem`` pipes that module to ``python3 -`` on the launch
target (over its ControlMaster when ``ssh_args`` configures one) — so path
confinement, the denylist, and every response shape are identical for local
and remote sessions.
"""

import asyncio
import base64
import binascii
import importlib.resources
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.workspace_preview import (
    SENTINEL,
    DirListing,
    FileContent,
    ResolvedPath,
    WalkResult,
    WorkspaceFileTooLargeError,
    WorkspacePathError,
    op_list_dir,
    op_list_dirs,
    op_read_file,
    op_resolve,
    op_walk_files,
    resolve_existing_file,
    run_git_commands,
)

DEFAULT_TIMEOUT_SECONDS = 20.0
# Remote raw reads travel base64-encoded in one JSON line; local ones stream.
REMOTE_RAW_MAX_BYTES = 25 * 1024 * 1024


class WorkspaceUnavailableError(Exception):
    """The session's workspace host can't be reached (or no longer exists)."""


@dataclass(frozen=True)
class RawFile:
    name: str
    path: Path | None = None
    data: bytes | None = None


class WorkspaceFilesystem(Protocol):
    async def list_dir(
        self, base: str, rel: str, cap: int, offset: int
    ) -> DirListing: ...

    async def walk_files(self, base: str) -> WalkResult: ...

    async def resolve(
        self, base: str, rel: str, must_exist: bool = True
    ) -> ResolvedPath: ...

    async def read_file(self, base: str, rel: str, max_bytes: int) -> FileContent: ...

    async def read_raw(self, base: str, rel: str) -> RawFile: ...

    async def git(
        self, base: str, commands: list[list[str]]
    ) -> list[tuple[int, bytes]]: ...

    async def list_dirs(self, prefix: str, limit: int) -> list[str]: ...


class LocalWorkspaceFilesystem:
    def __init__(self, denylist: list[str] | None, follow_symlinks: bool) -> None:
        self._denylist = denylist
        self._follow = follow_symlinks

    async def list_dir(self, base: str, rel: str, cap: int, offset: int) -> DirListing:
        return await asyncio.to_thread(
            op_list_dir, base, rel, cap, offset, self._denylist, self._follow
        )

    async def walk_files(self, base: str) -> WalkResult:
        return await asyncio.to_thread(
            op_walk_files, base, self._denylist, self._follow
        )

    async def resolve(
        self, base: str, rel: str, must_exist: bool = True
    ) -> ResolvedPath:
        return await asyncio.to_thread(
            op_resolve, base, rel, self._denylist, self._follow, must_exist
        )

    async def read_file(self, base: str, rel: str, max_bytes: int) -> FileContent:
        return await asyncio.to_thread(
            op_read_file, base, rel, max_bytes, self._denylist, self._follow
        )

    async def read_raw(self, base: str, rel: str) -> RawFile:
        path = await asyncio.to_thread(
            resolve_existing_file, Path(base), rel, self._denylist, self._follow
        )
        return RawFile(name=path.name, path=path)

    async def git(
        self, base: str, commands: list[list[str]]
    ) -> list[tuple[int, bytes]]:
        return await asyncio.to_thread(run_git_commands, base, commands)

    async def list_dirs(self, prefix: str, limit: int) -> list[str]:
        result = await asyncio.to_thread(op_list_dirs, prefix, limit, self._denylist)
        return result["directories"]


_SCRIPT_BYTES: bytes | None = None


def _script_bytes() -> bytes:
    global _SCRIPT_BYTES
    if _SCRIPT_BYTES is None:
        _SCRIPT_BYTES = (
            importlib.resources.files("waypoint")
            .joinpath("workspace_preview.py")
            .read_bytes()
        )
    return _SCRIPT_BYTES


class RemoteWorkspaceFilesystem:
    def __init__(
        self,
        launch_target: SshLaunchTargetConfig,
        denylist: list[str] | None,
        follow_symlinks: bool,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._target = launch_target
        self._denylist = denylist
        self._follow = follow_symlinks
        self._timeout = timeout_seconds

    async def list_dir(self, base: str, rel: str, cap: int, offset: int) -> DirListing:
        payload = await self._run(
            "list_dir",
            base=base,
            rel=rel,
            cap=cap,
            offset=offset,
            denylist=self._denylist,
            follow_symlinks=self._follow,
        )
        return cast(DirListing, payload)

    async def walk_files(self, base: str) -> WalkResult:
        payload = await self._run(
            "walk_files",
            base=base,
            denylist=self._denylist,
            follow_symlinks=self._follow,
        )
        return cast(WalkResult, payload)

    async def resolve(
        self, base: str, rel: str, must_exist: bool = True
    ) -> ResolvedPath:
        payload = await self._run(
            "resolve",
            base=base,
            rel=rel,
            denylist=self._denylist,
            follow_symlinks=self._follow,
            must_exist=must_exist,
        )
        return cast(ResolvedPath, payload)

    async def read_file(self, base: str, rel: str, max_bytes: int) -> FileContent:
        payload = await self._run(
            "read_file",
            base=base,
            rel=rel,
            max_bytes=max_bytes,
            denylist=self._denylist,
            follow_symlinks=self._follow,
        )
        return cast(FileContent, payload)

    async def read_raw(self, base: str, rel: str) -> RawFile:
        payload = await self._run(
            "read_raw",
            base=base,
            rel=rel,
            max_bytes=REMOTE_RAW_MAX_BYTES,
            denylist=self._denylist,
            follow_symlinks=self._follow,
        )
        return RawFile(name=payload["name"], data=_b64(payload["data_b64"]))

    async def git(
        self, base: str, commands: list[list[str]]
    ) -> list[tuple[int, bytes]]:
        payload = await self._run("git", base=base, commands=commands)
        return [(int(code), _b64(out)) for code, out in payload["results"]]

    async def list_dirs(self, prefix: str, limit: int) -> list[str]:
        payload = await self._run(
            "list_dirs", prefix=prefix, limit=limit, denylist=self._denylist
        )
        return list(payload["directories"])

    async def _run(self, op: str, **args: Any) -> dict[str, Any]:
        try:
            argv = self._target.build_remote_exec_args(
                ["python3", "-", op, json.dumps(args)]
            )
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise WorkspaceUnavailableError(f"cannot run remote {op!r}: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(_script_bytes()), timeout=self._timeout
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise WorkspaceUnavailableError(f"remote {op!r} timed out") from exc
        payload = _parse_payload(stdout)
        if payload is None:
            detail = stderr.decode("utf-8", errors="replace").strip()[:240]
            raise WorkspaceUnavailableError(
                f"remote {op!r} exited {process.returncode}: {detail}"
            )
        _raise_for_error(payload)
        return payload


def _parse_payload(stdout: bytes) -> dict[str, Any] | None:
    # Framed by a sentinel so a login shell's rcfile output can't corrupt it.
    text = stdout.decode("utf-8", errors="replace")
    index = text.find(SENTINEL)
    if index == -1:
        return None
    lines = text[index + len(SENTINEL) :].splitlines()
    try:
        payload = json.loads(lines[0]) if lines else None
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _raise_for_error(payload: dict[str, Any]) -> None:
    code = payload.get("error")
    if code is None:
        return
    detail = str(payload.get("detail", ""))
    if code == "denied":
        raise WorkspacePathError(detail)
    if code == "too_large":
        raise WorkspaceFileTooLargeError(detail)
    if code == "not_a_directory":
        raise NotADirectoryError(detail)
    if code == "not_found":
        raise FileNotFoundError(detail)
    raise WorkspaceUnavailableError(f"remote workspace op failed: {detail}")


def _b64(value: Any) -> bytes:
    try:
        return base64.b64decode(str(value), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WorkspaceUnavailableError("malformed remote payload") from exc
