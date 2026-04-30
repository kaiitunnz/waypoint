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
- Files smaller than ``MIN_TRANSCRIPT_BYTES`` are treated as empty/aborted
  sessions (Claude writes a few queue-operation lines before the user
  ever submits a prompt) and skipped.
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

log = logging.getLogger("waypoint.claude_threads")

# Sessions below this size only contain bookkeeping records (queue-op,
# attachment metadata) without a user prompt — not useful to resume.
MIN_TRANSCRIPT_BYTES = 1024

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


def claude_projects_root() -> Path:
    """Directory under which Claude stores per-project session transcripts.

    Mirrors the precedence the CLI uses: ``$CLAUDE_CONFIG_DIR`` overrides
    the default ``~/.claude``.
    """
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".claude"
    return root / "projects"


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


def _read_thread_info(path: Path) -> ClaudeThreadInfo | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size < MIN_TRANSCRIPT_BYTES:
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
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None
