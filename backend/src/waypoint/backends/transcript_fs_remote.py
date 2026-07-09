"""Remote (SSH) implementation of the TranscriptFilesystem seam.

``RemoteTranscriptFilesystem`` drives the exact same
:mod:`waypoint.backends.transcripts` policy code as the local implementation,
substituting SSH round-trips (over the existing ControlMaster — see
``launch_targets.SshLaunchTargetConfig``/``ssh_master.py``) for local
``pathlib``/``shutil`` calls. One vendored, stdlib-only helper script
(:mod:`waypoint.backends.transcript_fs_remote_script`, piped to ``python3 -``)
implements every operation, so there is one remote command surface to reason
about instead of a bespoke ``ssh`` invocation per op.

Fail-before-destroy: read-only queries (``exists``/``is_dir``/``is_symlink``/
``listdir``/``glob_artifacts``) degrade to a "not found" answer when the
round-trip itself fails (timeout, dropped connection, bad output) rather than
raising — the worst outcome is a wrong turn that itself degrades to a clean,
non-destructive :class:`~waypoint.backends.transcripts.TranscriptUnavailableError`
further down the policy (a stale symlink target compares unequal, a copy finds
no source, a final re-check reports the thread still unavailable). Mutating
ops (``mkdir``/``chmod``/``rmdir``/``symlink``/``copy_file``/``readlink``)
instead raise immediately on any failure — remote or transport — since there
is no safe default for "did the write happen".

``shared_transcript_dir`` remote-locality is validated implicitly, not with a
dedicated check: ``ensure_symlink_shared`` always calls ``fs.mkdir`` on it
first, so a dangling or unreachable shared dir (wrong host, bad permissions,
a path that only makes sense on the machine running waypoint) surfaces as a
loud ``TranscriptUnavailableError`` from that remote ``mkdir`` before any
symlink or copy is attempted — mirroring how the local implementation never
special-cases this either.
"""

import importlib.resources
import json
import logging
import subprocess
from typing import Any

from waypoint.backends.transcripts import TranscriptUnavailableError
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import SessionRecord

log = logging.getLogger("waypoint.backends.transcript_fs_remote")

SENTINEL = "__WP_FS_BEGIN__"
DEFAULT_TIMEOUT_SECONDS = 20.0

_SCRIPT_BYTES: bytes | None = None


def _script_bytes() -> bytes:
    global _SCRIPT_BYTES
    if _SCRIPT_BYTES is None:
        _SCRIPT_BYTES = (
            importlib.resources.files("waypoint.backends")
            .joinpath("transcript_fs_remote_script.py")
            .read_bytes()
        )
    return _SCRIPT_BYTES


class RemoteTranscriptFilesystem:
    """Drives :mod:`transcript_fs_remote_script` over ``launch_target``."""

    def __init__(
        self,
        launch_target: SshLaunchTargetConfig,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._launch_target = launch_target
        self._timeout = timeout_seconds

    # -- query ops: degrade to a safe "not found" answer on transport failure

    def exists(self, path: str) -> bool:
        return bool(self._run("exists", path).get("exists", False))

    def is_dir(self, path: str) -> bool:
        return bool(self._run("is_dir", path).get("is_dir", False))

    def is_symlink(self, path: str) -> bool:
        return bool(self._run("is_symlink", path).get("is_symlink", False))

    def listdir(self, path: str) -> list[str]:
        entries = self._run("listdir", path).get("entries", [])
        return entries if isinstance(entries, list) else []

    def glob_artifacts(
        self, session: SessionRecord, plugin: Any, config_dir: str
    ) -> list[str]:
        pattern = plugin.native_thread_artifact_glob(session)
        if pattern is None:
            return []
        paths = self._run("glob", config_dir, pattern).get("paths", [])
        return paths if isinstance(paths, list) else []

    # -- mutating ops: raise on any failure, transport or remote

    def readlink(self, path: str) -> str:
        result = self._run("readlink", path)
        target = result.get("target")
        if not isinstance(target, str):
            raise TranscriptUnavailableError(
                f"remote readlink failed for {path}: "
                f"{result.get('error', 'unknown error')}"
            )
        return target

    def mkdir(
        self, path: str, *, parents: bool = False, exist_ok: bool = False
    ) -> None:
        self._mutate("mkdir", path, "1" if parents else "0", "1" if exist_ok else "0")

    def chmod(self, path: str, mode: int) -> None:
        self._mutate("chmod", path, str(mode))

    def rmdir(self, path: str) -> None:
        self._mutate("rmdir", path)

    def symlink(self, path: str, target: str) -> None:
        self._mutate("symlink", path, target)

    def copy_file(self, src: str, dst: str, mode: int) -> None:
        self._mutate("copy_file", src, dst, str(mode))

    def expanduser(self, path: str) -> str:
        # Resolve ``~`` against the *remote* home (never the backend host's).
        # Absolute paths skip the round-trip. The policy logic downstream
        # (``relative_to``, symlink-target comparison) needs the same absolute
        # form the remote glob/symlink ops produce, so this must expand rather
        # than leave ``~`` for per-op expansion.
        if not path.startswith("~"):
            return path
        result = self._run("expanduser", path)
        expanded = result.get("path")
        if not isinstance(expanded, str) or expanded.startswith("~"):
            raise TranscriptUnavailableError(
                f"could not expand remote path {path!r}: "
                f"{result.get('error', 'unknown error')}"
            )
        return expanded

    # -- transport --------------------------------------------------------

    def _run(self, op: str, *args: str) -> dict[str, Any]:
        argv = self._launch_target.build_remote_exec_args(["python3", "-", op, *args])
        try:
            completed = subprocess.run(
                argv,
                input=_script_bytes(),
                capture_output=True,
                timeout=self._timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"error": f"transport failure running {op!r}: {exc}"}
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            return {
                "error": f"remote {op!r} exited {completed.returncode}: {stderr[:240]}"
            }
        stdout = completed.stdout.decode("utf-8", errors="replace")
        index = stdout.find(SENTINEL)
        if index == -1:
            return {"error": f"remote {op!r} produced no sentinel-framed output"}
        line = stdout[index + len(SENTINEL) :].strip().splitlines()[0:1]
        if not line:
            return {"error": f"remote {op!r} produced an empty payload"}
        try:
            payload = json.loads(line[0])
        except json.JSONDecodeError:
            return {"error": f"remote {op!r} produced non-JSON output"}
        if not isinstance(payload, dict):
            return {"error": f"remote {op!r} produced a malformed payload"}
        return payload

    def _mutate(self, op: str, *args: str) -> None:
        result = self._run(op, *args)
        if not result.get("ok"):
            raise TranscriptUnavailableError(
                f"remote transcript fs op {op!r} failed: "
                f"{result.get('error', 'unknown error')}"
            )
