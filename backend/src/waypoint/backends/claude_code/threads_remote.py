"""Remote (SSH) Claude transcript enumeration.

Drives ``backend/scripts/claude_thread_enumerator.sh`` against an SSH
target via ``bash -s`` (the script body is piped on stdin so it never
lands on disk on the remote, and argv stays small). Stdout starts with
``__WP_BEGIN__`` followed by one JSON object per resumable transcript.

All failure modes (non-zero exit, timeout, parse error, missing
sentinel, missing ``jq`` on remote) collapse to an empty list — the
list endpoint is a background poll and a 503 toast on every refresh
would be hostile UX. Each (target, error-class) pair is logged at
WARN exactly once per process so a broken remote doesn't flood the
log.
"""

import asyncio
import json
import logging
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from waypoint.backends.claude_code.remote import build_remote_thread_enumeration_args
from waypoint.backends.claude_code.threads import ClaudeThreadInfo
from waypoint.server_config import SshLaunchTargetConfig

log = logging.getLogger("waypoint.claude_threads_remote")

SENTINEL = "__WP_BEGIN__"
DEFAULT_TTL_SECONDS = 30.0
DEFAULT_TIMEOUT_SECONDS = 30.0


class RemoteClaudeThreadEnumerator:
    def __init__(
        self,
        helper_script_path: Path,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._helper_bytes = helper_script_path.read_bytes()
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds
        self._cache: dict[str, tuple[float, list[ClaudeThreadInfo]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._warned_classes: set[tuple[str, str]] = set()

    async def list(self, target: SshLaunchTargetConfig) -> list[ClaudeThreadInfo]:
        cached = self._cache.get(target.id)
        now = time.monotonic()
        if cached is not None and now - cached[0] < self._ttl:
            return cached[1]
        async with self._lock_for(target.id):
            cached = self._cache.get(target.id)
            now = time.monotonic()
            if cached is not None and now - cached[0] < self._ttl:
                return cached[1]
            stdout = await self._run_remote(target, env=None)
            if stdout is None:
                self._cache[target.id] = (now, [])
                return []
            results = _parse_records(stdout)
            self._cache[target.id] = (now, results)
            return results

    async def find(
        self, target: SshLaunchTargetConfig, thread_id: str
    ) -> ClaudeThreadInfo | None:
        stdout = await self._run_remote(target, env={"WAYPOINT_THREAD_ID": thread_id})
        if stdout is None:
            return None
        for info in _parse_records(stdout):
            if info.id == thread_id:
                return info
        return None

    def invalidate(self, launch_target_id: str) -> None:
        self._cache.pop(launch_target_id, None)

    def _lock_for(self, launch_target_id: str) -> asyncio.Lock:
        lock = self._locks.get(launch_target_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[launch_target_id] = lock
        return lock

    async def _run_remote(
        self,
        target: SshLaunchTargetConfig,
        *,
        env: dict[str, str] | None,
    ) -> str | None:
        # Building the argv resolves ``ssh_bin`` via shutil.which / explicit
        # path checks; both raise FileNotFoundError when the binary is
        # missing or misconfigured. Treat that the same as a runtime
        # failure so a misconfigured target degrades to an empty list with
        # a WARN log instead of a 500 from background poll endpoints.
        try:
            args = build_remote_thread_enumeration_args(target, env=env)
        except (FileNotFoundError, OSError) as exc:
            self._warn_once(
                target.id, "config-error", f"failed to build SSH argv: {exc}"
            )
            return None
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                args,
                input=self._helper_bytes,
                capture_output=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            self._warn_once(target.id, "timeout", "enumerator timed out")
            return None
        except OSError as exc:
            self._warn_once(target.id, "os-error", f"failed to spawn ssh: {exc}")
            return None
        if completed.returncode != 0:
            stderr_text = (
                (completed.stderr or b"").decode("utf-8", errors="replace").strip()
            )
            self._warn_once(
                target.id,
                f"exit-{completed.returncode}",
                f"enumerator exit {completed.returncode}: {stderr_text}",
            )
            return None
        stdout = completed.stdout.decode("utf-8", errors="replace")
        if SENTINEL not in stdout:
            self._warn_once(
                target.id,
                "no-sentinel",
                "enumerator stdout missing sentinel; rcfile contamination?",
            )
            return None
        return stdout

    def _warn_once(self, target_id: str, error_class: str, message: str) -> None:
        key = (target_id, error_class)
        if key in self._warned_classes:
            return
        self._warned_classes.add(key)
        log.warning(
            "claude thread enumerator failed",
            extra={"launch_target_id": target_id, "detail": message},
        )


def _parse_records(stdout: str) -> list[ClaudeThreadInfo]:
    # Sentinel detection happens in ``_run_remote`` so the failure path
    # routes through ``_warn_once`` for rate-limited dedupe; this function
    # therefore only ever sees stdout that contains the sentinel.
    sentinel_index = stdout.find(SENTINEL)
    if sentinel_index == -1:
        return []
    payload = stdout[sentinel_index + len(SENTINEL) :]
    results: list[ClaudeThreadInfo] = []
    for raw in payload.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        info = _record_to_info(record)
        if info is not None:
            results.append(info)
    results.sort(key=lambda info: info.updated_at, reverse=True)
    return results


def _record_to_info(record: object) -> ClaudeThreadInfo | None:
    if not isinstance(record, dict):
        return None
    thread_id = record.get("id")
    cwd = record.get("cwd")
    preview = record.get("preview")
    if not isinstance(thread_id, str) or not thread_id:
        return None
    if not isinstance(cwd, str) or not cwd:
        return None
    if not isinstance(preview, str) or not preview:
        return None
    branch = record.get("branch") if isinstance(record.get("branch"), str) else None
    title_raw = record.get("title")
    title = (
        title_raw.strip() if isinstance(title_raw, str) and title_raw.strip() else None
    )
    if title is None:
        title = preview.splitlines()[0][:80] or f"Claude {thread_id[:8]}"
    repo_name = Path(cwd).name or None
    mtime = record.get("mtime")
    if isinstance(mtime, int | float):
        updated_at = datetime.fromtimestamp(float(mtime), UTC)
    else:
        updated_at = datetime.now(UTC)
    first_ts_raw = record.get("first_ts")
    created_at = updated_at
    if isinstance(first_ts_raw, str):
        parsed = _parse_iso_timestamp(first_ts_raw)
        if parsed is not None:
            created_at = parsed
    return ClaudeThreadInfo(
        id=thread_id,
        cwd=cwd,
        title=title,
        branch=branch,
        repo_name=repo_name,
        preview=preview,
        created_at=created_at,
        updated_at=updated_at,
    )


def _parse_iso_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
