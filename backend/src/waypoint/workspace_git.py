import asyncio
from pathlib import Path

from pydantic import BaseModel

from waypoint.backends.diff_preview import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_TOTAL_BYTES,
    DiffPreviewFile,
    DiffPreviewPayload,
    build_preview,
    file_from_old_new,
    files_from_unified_diff,
    unavailable_file,
)
from waypoint.workspace_preview import read_text_capped


class GitFileStatus(BaseModel):
    path: str
    old_path: str | None = None
    # The two porcelain v1 status columns: ``index`` (staged) and ``worktree``
    # (unstaged). A space means unmodified in that area; ``?`` marks untracked.
    index_status: str
    worktree_status: str
    untracked: bool


class GitStatus(BaseModel):
    branch: str | None
    files: list[GitFileStatus]


async def _run_git(cwd: Path, *args: str) -> tuple[int, bytes]:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return 127, b""
    stdout, _ = await process.communicate()
    return process.returncode if process.returncode is not None else -1, stdout


async def _git_text(cwd: Path, *args: str) -> str | None:
    code, out = await _run_git(cwd, *args)
    if code != 0:
        return None
    return out.decode("utf-8", errors="replace").strip() or None


async def is_git_repo(base: Path) -> bool:
    code, out = await _run_git(base, "rev-parse", "--is-inside-work-tree")
    return code == 0 and out.decode("utf-8", errors="replace").strip() == "true"


async def git_status(base: Path) -> GitStatus | None:
    if not await is_git_repo(base):
        return None
    branch = await _git_text(base, "rev-parse", "--abbrev-ref", "HEAD")
    # ``base`` may be a subdirectory of the repo; porcelain paths are always
    # repo-root-relative, so translate them to ``base``-relative and drop
    # entries living outside the browsed subtree.
    prefix = await _git_text(base, "rev-parse", "--show-prefix") or ""
    code, raw = await _run_git(
        base, "status", "--porcelain=v1", "-z", "--untracked-files=all"
    )
    if code != 0:
        return None
    scoped: list[GitFileStatus] = []
    for entry in _parse_porcelain_z(raw):
        rel = _strip_prefix(entry.path, prefix)
        if rel is None:
            continue
        old_rel = _strip_prefix(entry.old_path, prefix) if entry.old_path else None
        scoped.append(entry.model_copy(update={"path": rel, "old_path": old_rel}))
    return GitStatus(branch=branch, files=scoped)


async def git_file_diff(
    base: Path,
    rel: str,
    *,
    staged: bool,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> DiffPreviewPayload | None:
    # ``staged`` shows only the index-vs-HEAD slice; otherwise the combined
    # working-tree-vs-HEAD view (staged + unstaged together).
    # ``-U1000000`` forces git to emit the entire file as context (clamped to
    # the file length) so the frontend can render full-file inline diffs.
    diff_args = ("-U1000000", "--cached") if staged else ("-U1000000", "HEAD")
    diff = await _git_diff(base, *diff_args, "--", rel)
    if diff and diff.strip():
        files = files_from_unified_diff(diff, fallback_path=rel)
    else:
        files = await _untracked_add(base, rel, max_file_bytes)
    if not files:
        return None
    return build_preview("aggregate", files, max_file_bytes, max_total_bytes)


async def _git_diff(base: Path, *args: str) -> str | None:
    code, out = await _run_git(base, "diff", *args)
    if code != 0:
        return None
    return out.decode("utf-8", errors="replace")


async def _untracked_add(
    base: Path, rel: str, max_file_bytes: int
) -> list[DiffPreviewFile]:
    code, out = await _run_git(base, "status", "--porcelain=v1", "-z", "--", rel)
    if code != 0:
        return []
    first = out.decode("utf-8", errors="replace").split("\0", 1)[0]
    if not first.startswith("??"):
        return []
    target = base.expanduser() / rel
    try:
        content, _truncated, binary, _encoding = read_text_capped(
            target, max_file_bytes
        )
    except OSError:
        return []
    if binary or content is None:
        return [unavailable_file(rel, "Untracked file is binary or too large", "add")]
    return [file_from_old_new(rel, "", content, "add")]


def _parse_porcelain_z(raw: bytes) -> list[GitFileStatus]:
    tokens = raw.decode("utf-8", errors="replace").split("\0")
    files: list[GitFileStatus] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if len(token) < 3:
            continue
        status_field = token[:2]
        path = token[3:]
        old_path: str | None = None
        # A rename/copy in the index emits the source path as the next token.
        if status_field[0] in ("R", "C") and index < len(tokens):
            old_path = tokens[index]
            index += 1
        files.append(
            GitFileStatus(
                path=path,
                old_path=old_path,
                index_status=status_field[0],
                worktree_status=status_field[1],
                untracked=status_field == "??",
            )
        )
    return files


def _strip_prefix(path: str, prefix: str) -> str | None:
    if not prefix:
        return path
    if path.startswith(prefix):
        return path[len(prefix) :]
    return None
