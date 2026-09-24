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
from waypoint.workspace_fs import WorkspaceFilesystem
from waypoint.workspace_preview import WorkspacePathError


class GitFileStatus(BaseModel):
    path: str
    old_path: str | None = None
    # The two porcelain v1 status columns: ``index`` (staged) and ``worktree``
    # (unstaged). A space means unmodified in that area; ``?`` marks untracked.
    index_status: str
    worktree_status: str
    untracked: bool


class GitStatus(BaseModel):
    # ``branch`` is the display label for HEAD: a branch name when on a branch,
    # otherwise (detached) the exact tag, ``git describe`` output, or short SHA.
    branch: str | None
    detached: bool = False
    files: list[GitFileStatus]


_REPO_CHECK = ["rev-parse", "--is-inside-work-tree"]


def _text(result: tuple[int, bytes]) -> str | None:
    code, out = result
    if code != 0:
        return None
    return out.decode("utf-8", errors="replace").strip() or None


async def _head_label(
    fs: WorkspaceFilesystem, base: str, abbrev: str | None
) -> tuple[str | None, bool]:
    # On a branch, ``--abbrev-ref HEAD`` is the branch name. A detached HEAD
    # (``git checkout`` of a tag or commit) reports the literal ``HEAD``, and an
    # unborn branch fails outright; resolve a friendlier label so the UI never
    # shows the bare word "HEAD".
    if abbrev and abbrev != "HEAD":
        return abbrev, False
    exact, described, symbolic = await fs.git(
        base,
        [
            ["describe", "--tags", "--exact-match", "HEAD"],
            ["describe", "--tags", "--always", "HEAD"],
            # Unborn branch (repo with no commits yet): no commit to describe,
            # but the symbolic target still names the branch HEAD will create.
            ["symbolic-ref", "--short", "HEAD"],
        ],
    )
    for label, detached in ((exact, True), (described, True), (symbolic, False)):
        text = _text(label)
        if text:
            return text, detached
    return None, False


async def git_status(fs: WorkspaceFilesystem, base: str) -> GitStatus | None:
    repo, abbrev, prefix, status = await fs.git(
        base,
        [
            _REPO_CHECK,
            ["rev-parse", "--abbrev-ref", "HEAD"],
            ["rev-parse", "--show-prefix"],
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        ],
    )
    if _text(repo) != "true":
        return None
    branch, detached = await _head_label(fs, base, _text(abbrev))
    # ``base`` may be a subdirectory of the repo; porcelain paths are always
    # repo-root-relative, so translate them to ``base``-relative and drop
    # entries living outside the browsed subtree.
    prefix_text = _text(prefix) or ""
    code, raw = status
    if code != 0:
        return None
    scoped: list[GitFileStatus] = []
    for entry in _parse_porcelain_z(raw):
        rel = _strip_prefix(entry.path, prefix_text)
        if rel is None:
            continue
        old_rel = _strip_prefix(entry.old_path, prefix_text) if entry.old_path else None
        scoped.append(entry.model_copy(update={"path": rel, "old_path": old_rel}))
    return GitStatus(branch=branch, detached=detached, files=scoped)


async def git_list_files(fs: WorkspaceFilesystem, base: str) -> list[str] | None:
    # Tracked plus untracked-but-not-ignored files, ``base``-relative. ``git
    # ls-files`` scopes to the cwd subtree and respects ``.gitignore``, so this
    # skips ``node_modules``/build dirs for free. Returns ``None`` outside a repo
    # so the caller can fall back to a filesystem walk.
    repo, *listings = await fs.git(
        base,
        [
            _REPO_CHECK,
            ["ls-files", "-z"],
            ["ls-files", "-z", "--others", "--exclude-standard"],
        ],
    )
    if _text(repo) != "true":
        return None
    paths: list[str] = []
    seen: set[str] = set()
    for code, raw in listings:
        if code != 0:
            continue
        for token in raw.decode("utf-8", errors="replace").split("\0"):
            if token and token not in seen:
                seen.add(token)
                paths.append(token)
    return paths


async def git_file_diff(
    fs: WorkspaceFilesystem,
    base: str,
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
    diff_args = ["-U1000000", "--cached"] if staged else ["-U1000000", "HEAD"]
    [result] = await fs.git(base, [["diff", *diff_args, "--", rel]])
    code, out = result
    diff = out.decode("utf-8", errors="replace") if code == 0 else None
    if diff and diff.strip():
        files = files_from_unified_diff(diff, fallback_path=rel)
    else:
        files = await _untracked_add(fs, base, rel, max_file_bytes)
    if not files:
        return None
    return build_preview("aggregate", files, max_file_bytes, max_total_bytes)


async def _untracked_add(
    fs: WorkspaceFilesystem, base: str, rel: str, max_file_bytes: int
) -> list[DiffPreviewFile]:
    [(code, out)] = await fs.git(base, [["status", "--porcelain=v1", "-z", "--", rel]])
    if code != 0:
        return []
    first = out.decode("utf-8", errors="replace").split("\0", 1)[0]
    if not first.startswith("??"):
        return []
    try:
        read = await fs.read_file(base, rel, max_file_bytes)
    except (WorkspacePathError, OSError):
        return []
    content = read["content"]
    if read["binary"] or content is None:
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
