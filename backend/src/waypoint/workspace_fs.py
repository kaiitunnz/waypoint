"""Workspace operations for a session, run in-process for local sessions or on
the SSH launch target for remote ones, both via :mod:`waypoint.workspace_preview`.
"""

import asyncio
import base64
import binascii
import importlib.resources
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, cast

from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.workspace_preview import (
    ERROR_CODES,
    OPS,
    SENTINEL,
    DirListing,
    FileContent,
    ResolvedPath,
    WalkResult,
    resolve_existing_file,
    run_git_commands,
)

TIMEOUT_SECONDS = 20.0
# A remote raw read returns the whole file base64-encoded in one JSON line.
REMOTE_RAW_MAX_BYTES = 25 * 1024 * 1024


class WorkspaceUnavailableError(Exception):
    """The session's workspace host is unreachable, unknown, or failed the operation."""


@dataclass(frozen=True)
class RawFile:
    name: str
    content: Path | bytes


class WorkspaceFilesystem(ABC):
    def __init__(self, denylist: list[str] | None, follow_symlinks: bool) -> None:
        self._denylist = denylist
        self._follow = follow_symlinks

    @abstractmethod
    async def _call(self, op: str, **args: Any) -> Any: ...

    @abstractmethod
    async def read_raw(self, base: str, rel: str) -> RawFile: ...

    @abstractmethod
    async def git(
        self, base: str, commands: list[list[str]]
    ) -> list[tuple[int, bytes]]: ...

    async def list_dir(self, base: str, rel: str, cap: int, offset: int) -> DirListing:
        return cast(
            DirListing,
            await self._call(
                "list_dir",
                base=base,
                rel=rel,
                cap=cap,
                offset=offset,
                denylist=self._denylist,
                follow_symlinks=self._follow,
            ),
        )

    async def walk_files(self, base: str) -> WalkResult:
        return cast(
            WalkResult,
            await self._call(
                "walk_files",
                base=base,
                denylist=self._denylist,
                follow_symlinks=self._follow,
            ),
        )

    async def resolve(
        self, base: str, rel: str, must_exist: bool = True
    ) -> ResolvedPath:
        return cast(
            ResolvedPath,
            await self._call(
                "resolve",
                base=base,
                rel=rel,
                denylist=self._denylist,
                follow_symlinks=self._follow,
                must_exist=must_exist,
            ),
        )

    async def read_file(self, base: str, rel: str, max_bytes: int) -> FileContent:
        return cast(
            FileContent,
            await self._call(
                "read_file",
                base=base,
                rel=rel,
                max_bytes=max_bytes,
                denylist=self._denylist,
                follow_symlinks=self._follow,
            ),
        )

    async def list_dirs(self, prefix: str, limit: int) -> list[str]:
        result = await self._call(
            "list_dirs", prefix=prefix, limit=limit, denylist=self._denylist
        )
        return list(result["directories"])


class LocalWorkspaceFilesystem(WorkspaceFilesystem):
    async def _call(self, op: str, **args: Any) -> Any:
        return await asyncio.to_thread(OPS[op], **args)

    async def read_raw(self, base: str, rel: str) -> RawFile:
        path = await asyncio.to_thread(
            resolve_existing_file, Path(base), rel, self._denylist, self._follow
        )
        return RawFile(name=path.name, content=path)

    async def git(
        self, base: str, commands: list[list[str]]
    ) -> list[tuple[int, bytes]]:
        return await asyncio.to_thread(run_git_commands, base, commands)


class RemoteWorkspaceFilesystem(WorkspaceFilesystem):
    def __init__(
        self,
        launch_target: SshLaunchTargetConfig,
        denylist: list[str] | None,
        follow_symlinks: bool,
    ) -> None:
        super().__init__(denylist, follow_symlinks)
        self._target = launch_target

    async def read_raw(self, base: str, rel: str) -> RawFile:
        payload = await self._call(
            "read_raw",
            base=base,
            rel=rel,
            max_bytes=REMOTE_RAW_MAX_BYTES,
            denylist=self._denylist,
            follow_symlinks=self._follow,
        )
        return RawFile(name=payload["name"], content=_b64(payload["data_b64"]))

    async def git(
        self, base: str, commands: list[list[str]]
    ) -> list[tuple[int, bytes]]:
        payload = await self._call("git", base=base, commands=commands)
        return [(int(code), _b64(out)) for code, out in payload["results"]]

    async def _call(self, op: str, **args: Any) -> Any:
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
                process.communicate(_script_bytes()), timeout=TIMEOUT_SECONDS
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


@cache
def _script_bytes() -> bytes:
    return (
        importlib.resources.files("waypoint")
        .joinpath("workspace_preview.py")
        .read_bytes()
    )


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
    for error_class, error_code in ERROR_CODES:
        if code == error_code:
            raise error_class(detail)
    raise WorkspaceUnavailableError(f"remote workspace op failed: {detail}")


def _b64(value: Any) -> bytes:
    try:
        return base64.b64decode(str(value), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise WorkspaceUnavailableError("malformed remote payload") from exc
