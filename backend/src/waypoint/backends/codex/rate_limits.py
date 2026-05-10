from __future__ import annotations

import asyncio
import errno
import os
import re
import select
import shutil
import signal
import subprocess
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from waypoint.schemas import SessionRateLimitUsage, UsageWindow

_WINDOW_LABELS: dict[str, tuple[str, int]] = {
    "5h limit": ("5h", 5 * 60),
    "weekly limit": ("Weekly", 7 * 24 * 60),
}
_PERCENT_LEFT_RE = re.compile(r"(?i)(\d{1,3}(?:\.\d+)?)\s*%\s*left")
_PERCENT_USED_RE = re.compile(r"(?i)(\d{1,3}(?:\.\d+)?)\s*%\s*used")
_CREDITS_RE = re.compile(r"(?i)credits:\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)")


def parse_codex_status(
    text: str, *, now: datetime | None = None
) -> SessionRateLimitUsage | None:
    clean = _strip_ansi(text).strip()
    if not clean:
        return None

    windows: list[UsageWindow] = []
    for line in clean.splitlines():
        lowered = line.lower()
        for needle, (label, minutes) in _WINDOW_LABELS.items():
            if needle not in lowered:
                continue
            percent = _parse_percent_used(line)
            if percent is None:
                continue
            reset_description = _extract_reset_description(line)
            windows.append(
                UsageWindow(
                    id=needle.replace(" ", "-"),
                    label=label,
                    used_percent=percent,
                    window_minutes=minutes,
                    reset_description=reset_description,
                )
            )
            break

    credits_remaining = None
    credits_currency = None
    credits_line = _first_matching_line(clean, "credits:")
    if credits_line is not None:
        match = _CREDITS_RE.search(credits_line)
        if match is not None:
            try:
                credits_remaining = float(match.group(1).replace(",", ""))
            except ValueError:
                credits_remaining = None
            credits_currency = "credits"
            if "$" in credits_line:
                credits_currency = "USD"

    if not windows and credits_remaining is None:
        return None

    return SessionRateLimitUsage(
        source="codex",
        updated_at=now or datetime.now(UTC),
        windows=windows,
        credits_remaining=credits_remaining,
        credits_currency=credits_currency,
        notes=["CLI status"],
    )


async def probe_codex_status(
    *,
    cwd: str,
    binary: str,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 8.0,
) -> SessionRateLimitUsage | None:
    resolved = _resolve_binary(binary)
    if resolved is None:
        return None
    text = await asyncio.to_thread(
        _run_codex_status,
        resolved,
        cwd,
        env if env is not None else dict(os.environ),
        timeout_seconds,
    )
    return parse_codex_status(text)


def _run_codex_status(
    binary: str,
    cwd: str,
    env: dict[str, str],
    timeout_seconds: float,
) -> str:
    import pty

    master_fd, slave_fd = pty.openpty()
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            [binary],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=str(Path(cwd).expanduser()),
            env=env,
            start_new_session=True,
        )
    except Exception:
        os.close(master_fd)
        os.close(slave_fd)
        raise

    os.close(slave_fd)
    try:
        time.sleep(0.35)
        try:
            os.write(master_fd, b"/status\r")
        except OSError:
            pass

        deadline = time.monotonic() + timeout_seconds
        settled_at: float | None = None
        buffer = bytearray()
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError as exc:
                    if exc.errno in {errno.EIO, errno.EBADF}:
                        break
                    raise
                if chunk:
                    buffer.extend(chunk)
                    settled_at = None
                    continue
                break
            if proc.poll() is not None:
                if settled_at is None:
                    settled_at = time.monotonic() + 0.5
                elif time.monotonic() >= settled_at:
                    break
        return buffer.decode("utf-8", errors="replace")
    finally:
        if proc is not None and proc.poll() is None:
            with suppress(Exception):
                os.write(master_fd, b"/exit\r")
            with suppress(Exception):
                proc.terminate()
            deadline = time.monotonic() + 1.0
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if proc.poll() is None:
                with suppress(Exception):
                    os.kill(proc.pid, signal.SIGKILL)
        with suppress(Exception):
            os.close(master_fd)


def _resolve_binary(binary: str) -> str | None:
    if not binary:
        return None
    if "/" in binary:
        path = Path(binary).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(binary)


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _first_matching_line(text: str, needle: str) -> str | None:
    for line in text.splitlines():
        if needle in line.lower():
            return line
    return None


def _parse_percent_used(line: str) -> float | None:
    match = _PERCENT_USED_RE.search(line)
    if match is not None:
        try:
            return _clamp_percent(float(match.group(1)))
        except ValueError:
            return None
    match = _PERCENT_LEFT_RE.search(line)
    if match is not None:
        try:
            return _clamp_percent(100.0 - float(match.group(1)))
        except ValueError:
            return None
    return None


def _clamp_percent(percent: float) -> float:
    return max(0.0, min(100.0, percent))


def _extract_reset_description(line: str) -> str | None:
    match = re.search(r"(?i)\breset(?:s)?(?:\s+at|\s+in)?\s*(.*)$", line)
    if match is not None:
        candidate = match.group(1).strip(" :.-\t)")
        return candidate or None
    parens = re.findall(r"\(([^()]*)\)", line)
    if parens:
        candidate = parens[-1].strip(" :.-\t)")
        return candidate or None
    return None
