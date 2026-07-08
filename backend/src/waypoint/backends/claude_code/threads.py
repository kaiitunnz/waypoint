"""On-disk discovery of resumable Claude Code sessions.

Claude Code's stream-json control protocol does NOT expose a thread/list
RPC (see ``tmp/docs/BACKEND_CONTROL_PROTOCOLS.md``). The CLI does, however,
persist every session as a JSONL transcript at
``$CLAUDE_CONFIG_DIR/projects/<encoded-cwd>/<session-uuid>.jsonl``
(default ``~/.claude/projects/``). This module enumerates those files and
extracts the metadata Waypoint needs to surface an "import existing
thread" workflow analogous to the Codex one.

Notes:
- The encoded-cwd directory name is lossy (``[^a-zA-Z0-9] -> '-'``); do
  NOT reverse it. Read ``cwd`` from the JSONL records themselves.
- Transcripts without a parsed user record are skipped — that's the
  only signal needed to filter out aborted/bookkeeping-only sessions.
- We stop scanning each transcript as soon as we have title, cwd, branch,
  and a preview; transcripts can be many MB.
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("waypoint.claude_threads")

# Cap how many lines we read per transcript before giving up on metadata.
# Real sessions surface cwd / title / first user message in the first few
# dozen records; scanning further wastes IO on multi-MB files.
MAX_LINES_SCANNED = 200

# Cap the byte length of any single line we attempt to parse. Transcripts
# can carry tool-result blobs that exceed Python's default line buffer;
# we don't need them for the summary.
MAX_LINE_BYTES = 256 * 1024

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass
class ClaudeThreadInfo:
    id: str
    cwd: str
    title: str
    branch: str | None
    repo_name: str | None
    preview: str | None
    created_at: datetime
    updated_at: datetime


def claude_projects_root(config_dir: str | None = None) -> Path:
    """Directory under which Claude stores per-project session transcripts.

    Mirrors the precedence the CLI uses: an explicit ``config_dir`` (e.g. a
    target account profile's) wins, else ``$CLAUDE_CONFIG_DIR`` overrides the
    default ``~/.claude``.
    """
    base = config_dir or os.environ.get("CLAUDE_CONFIG_DIR")
    root = Path(base).expanduser() if base else Path.home() / ".claude"
    return root / "projects"


def claude_config_file(config_dir: str) -> Path:
    """Path to the config file the CLI uses when ``CLAUDE_CONFIG_DIR`` is set.

    With ``CLAUDE_CONFIG_DIR`` exported the CLI keeps ``.claude.json`` *inside*
    that dir (``<config_dir>/.claude.json``), not at the unset-default home
    location ``~/.claude.json``. Account profiles always export the var, so a
    profile's onboarding/config state lives here.
    """
    return Path(config_dir).expanduser() / ".claude.json"


def claude_onboarding_complete(config_dir: str) -> bool:
    """Whether ``<config_dir>/.claude.json`` marks first-run onboarding as done.

    ``hasCompletedOnboarding`` is the flag the CLI sets once its first-run wizard
    (theme picker, login method) is dismissed. A profile dir lacking it — missing
    file, unreadable, or flag falsy — relaunches into that wizard, which a
    tmux/tty-driven turn cannot dismiss and so hangs. Returns ``False`` on any
    read/parse failure.
    """
    try:
        data = json.loads(claude_config_file(config_dir).read_text())
    except (OSError, ValueError):
        return False
    return bool(isinstance(data, dict) and data.get("hasCompletedOnboarding"))


def encode_project_dir(cwd: str) -> str:
    """Encode a cwd to the project-dir name Claude stores transcripts under.

    Matches the CLI's lossy ``[^a-zA-Z0-9] -> '-'`` mapping (e.g. a leading-dot
    component collapses ``/.`` to ``--``); the result is not reversible, which is
    why discovery reads ``cwd`` from the records rather than decoding the name.
    """
    return re.sub(r"[^a-zA-Z0-9]", "-", cwd)


def list_local_claude_threads() -> list[ClaudeThreadInfo]:
    """Enumerate resumable Claude sessions on the local filesystem.

    Returns one entry per ``<uuid>.jsonl`` transcript that has at least
    one user prompt. Sorted by ``updated_at`` descending.
    """
    root = claude_projects_root()
    if not root.is_dir():
        return []
    results: list[ClaudeThreadInfo] = []
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        for transcript in project_dir.glob("*.jsonl"):
            info = _read_thread_info(transcript)
            if info is not None:
                results.append(info)
    results.sort(key=lambda info: info.updated_at, reverse=True)
    return results


def local_claude_thread_artifacts(
    thread_id: str, config_dir: str | None = None
) -> list[Path]:
    """The on-disk transcript file(s) for ``thread_id`` under ``config_dir``.

    Scans every encoded-cwd project dir (the encoding is lossy, so the exact
    dir isn't derivable) for ``<thread_id>.jsonl``. Returns the matching paths
    (normally one), or ``[]`` when the thread isn't present under this config
    dir — the signal a target profile can't yet see it. UUID-guarded.
    """
    if not UUID_RE.match(thread_id):
        return []
    root = claude_projects_root(config_dir)
    if not root.is_dir():
        return []
    return [
        project_dir / f"{thread_id}.jsonl"
        for project_dir in root.iterdir()
        if project_dir.is_dir() and (project_dir / f"{thread_id}.jsonl").is_file()
    ]


def find_local_claude_thread(thread_id: str) -> ClaudeThreadInfo | None:
    """Locate a single transcript by its session UUID, regardless of which
    encoded-cwd directory it landed in."""
    if not UUID_RE.match(thread_id):
        return None
    root = claude_projects_root()
    if not root.is_dir():
        return None
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / f"{thread_id}.jsonl"
        if not candidate.is_file():
            continue
        info = _read_thread_info(candidate)
        if info is not None:
            return info
    return None


def delete_local_claude_thread(thread_id: str) -> bool:
    """Remove a single transcript by its session UUID, regardless of which
    encoded-cwd directory it landed in. The UUID check also guards against
    path/shell injection. Returns True iff a file was removed."""
    if not UUID_RE.match(thread_id):
        return False
    root = claude_projects_root()
    if not root.is_dir():
        return False
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / f"{thread_id}.jsonl"
        if not candidate.is_file():
            continue
        try:
            candidate.unlink()
        except OSError as exc:
            log.warning("failed to delete claude transcript %s: %s", candidate, exc)
            return False
        return True
    return False


def _read_thread_info(path: Path) -> ClaudeThreadInfo | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    session_id = path.stem
    if not UUID_RE.match(session_id):
        return None
    cwd: str | None = None
    title: str | None = None
    branch: str | None = None
    preview: str | None = None
    created_ts: float | None = None
    has_user_message = False
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line_index, line in enumerate(fh):
                if line_index >= MAX_LINES_SCANNED:
                    break
                if len(line) > MAX_LINE_BYTES:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                if cwd is None:
                    raw_cwd = record.get("cwd")
                    if isinstance(raw_cwd, str) and raw_cwd:
                        cwd = raw_cwd
                if branch is None:
                    raw_branch = record.get("gitBranch")
                    if isinstance(raw_branch, str) and raw_branch:
                        branch = raw_branch
                if created_ts is None:
                    raw_ts = record.get("timestamp")
                    if isinstance(raw_ts, str):
                        parsed = _parse_iso_timestamp(raw_ts)
                        if parsed is not None:
                            created_ts = parsed
                if title is None:
                    raw_title = record.get("customTitle") or record.get("aiTitle")
                    if isinstance(raw_title, str) and raw_title.strip():
                        title = raw_title.strip()
                if not has_user_message and record.get("type") == "user":
                    text = _extract_user_text(record.get("message"))
                    if text:
                        has_user_message = True
                        if preview is None:
                            preview = text
                if (
                    cwd is not None
                    and title is not None
                    and preview is not None
                    and branch is not None
                ):
                    break
    except OSError as exc:
        log.warning("failed to read claude transcript %s: %s", path, exc)
        return None
    if cwd is None or not has_user_message:
        return None
    final_title = title or _fallback_title(preview, cwd, session_id)
    repo_name = Path(cwd).name or None
    updated_at = datetime.fromtimestamp(stat.st_mtime, UTC)
    if created_ts is not None:
        created_at = datetime.fromtimestamp(created_ts, UTC)
    else:
        created_at = datetime.fromtimestamp(stat.st_ctime, UTC)
    return ClaudeThreadInfo(
        id=session_id,
        cwd=cwd,
        title=final_title,
        branch=branch,
        repo_name=repo_name,
        preview=preview,
        created_at=created_at,
        updated_at=updated_at,
    )


def _extract_user_text(message: object) -> str | None:
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
        return text or None
    if isinstance(content, list):
        parts: list[str] = []
        for entry in content:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "text":
                value = entry.get("text")
                if isinstance(value, str):
                    parts.append(value)
        joined = "\n".join(parts).strip()
        return joined or None
    return None


def _fallback_title(preview: str | None, cwd: str, session_id: str) -> str:
    if preview:
        first_line = preview.splitlines()[0].strip()
        if first_line:
            return first_line[:80]
    folder = Path(cwd).name
    if folder:
        return f"Claude {folder}"
    return f"Claude {session_id[:8]}"


def _parse_iso_timestamp(value: str) -> float | None:
    parsed = parse_iso_timestamp(value)
    return parsed.timestamp() if parsed is not None else None


def parse_iso_timestamp(value: str) -> datetime | None:
    """Parse a transcript record's ISO-8601 ``timestamp`` field.

    Shared with the history converter so seeded events preserve the
    source timestamp rather than the import time.
    """
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def read_local_claude_transcript(thread_id: str) -> list[dict[str, Any]]:
    """Read every record of a local Claude transcript, in file order.

    Unlike ``_read_thread_info`` (which stops after ``MAX_LINES_SCANNED``
    once it has enough metadata for a thread-list entry), this reads the
    transcript in full: thread-history import needs every user/assistant/
    tool record the CLI wrote, not just the first ones.
    """
    if not UUID_RE.match(thread_id):
        return []
    root = claude_projects_root()
    if not root.is_dir():
        return []
    for project_dir in root.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / f"{thread_id}.jsonl"
        if not candidate.is_file():
            continue
        return _read_all_records(candidate)
    return []


def _read_all_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError as exc:
        log.warning("failed to read claude transcript %s: %s", path, exc)
        return []
    return records
