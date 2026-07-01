"""Claude CLI version detection.

The model catalogue Claude offers for a new session depends on which CLI
build is actually installed: older binaries lack models/effort levels that
newer ones ship, and advertising an option the binary doesn't recognize means
it either rejects the flag or silently downgrades. This module probes the
local ``claude --version`` output, with a short TTL cache so repeated
model-list requests don't re-spawn the binary.

Version detection over SSH is out of scope: every function here accepts an
optional launch target and returns ``None`` (unknown) whenever one is given,
without touching the network. Callers should treat ``None`` as "assume
latest".
"""

import re
import subprocess
import time

from waypoint.launch_targets import SshLaunchTargetConfig

# How long a probed version stays valid before we re-run the binary. Long
# enough to avoid spawning a subprocess on every model-list request; short
# enough that an in-place CLI upgrade is picked up without a server restart.
_VERSION_CACHE_TTL_SECONDS = 300.0

# Raw version-string cache, keyed by binary path. Shared by
# ``claude_cli_version_string`` and ``detect_claude_cli_version`` so both
# callers (the rate-limit User-Agent and the model-catalogue gate) pay for at
# most one subprocess spawn per binary per TTL window.
_VERSION_STRING_CACHE: dict[str, tuple[float, str | None]] = {}

_VERSION_SEGMENT_RE = re.compile(r"\d+(?:\.\d+)+")


def _probe_claude_version_string(binary: str) -> str | None:
    """Run ``<binary> --version`` and extract the raw version substring.

    Prefers a dotted-numeric version, optionally with a ``-``/``+``
    pre-release or build suffix (e.g. ``2.1.197-beta.1``); falls back to the
    first whitespace-separated token of stdout when that regex doesn't
    match. Returns ``None`` on any failure (missing binary, non-zero exit,
    timeout) or empty output.
    """
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    match = re.search(r"\d+(?:\.\d+)+(?:[-+][A-Za-z0-9.-]+)?", completed.stdout)
    if match is not None:
        return match.group(0)
    stripped = completed.stdout.strip()
    return stripped.split()[0] if stripped else None


def _cached_version_string(binary: str) -> str | None:
    now = time.monotonic()
    cached = _VERSION_STRING_CACHE.get(binary)
    if cached is not None and now - cached[0] < _VERSION_CACHE_TTL_SECONDS:
        return cached[1]
    value = _probe_claude_version_string(binary)
    _VERSION_STRING_CACHE[binary] = (now, value)
    return value


def claude_cli_version_string(
    binary: str = "claude",
    launch_target: SshLaunchTargetConfig | None = None,
) -> str | None:
    """TTL-cached raw ``claude --version`` string, or ``None`` if undetectable.

    Remote (SSH) targets are out of scope for version detection: passing a
    ``launch_target`` always returns ``None`` without spawning a process.
    """
    if launch_target is not None:
        return None
    return _cached_version_string(binary)


def detect_claude_cli_version(
    binary: str = "claude",
    launch_target: SshLaunchTargetConfig | None = None,
) -> tuple[int, ...] | None:
    """Best-effort ``claude --version``, parsed into an int tuple (e.g. ``(2, 1, 197)``).

    Returns ``None`` when the binary can't be probed or its output doesn't
    parse — including for any non-``None`` ``launch_target``, since version
    detection over SSH is out of scope. Callers should treat ``None`` as
    "assume latest".
    """
    raw = claude_cli_version_string(binary, launch_target)
    if raw is None:
        return None
    match = _VERSION_SEGMENT_RE.match(raw)
    if match is None:
        return None
    try:
        return tuple(int(part) for part in match.group(0).split("."))
    except ValueError:
        return None
