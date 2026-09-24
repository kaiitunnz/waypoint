"""Workspace operations (``op_*``) for local and remote sessions.

Local sessions call the ops in-process; remote sessions pipe this file to
``python3 -`` on the SSH target, where ``_main`` runs one op and prints one
sentinel-framed JSON line. The module is stdlib-only and Python 3.8-compatible.
"""

# Required for PEP 585/604 annotations on a remote Python 3.8 interpreter.
from __future__ import annotations

import base64
import fnmatch
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypedDict

# The denylist is the single filter knob. By default only VCS internals and
# SSH key material are hidden; ordinary dotfiles (.env, .gitignore, …) preview
# fine. Add globs like ".*" to hide all dotfiles, or set the denylist to an
# explicit empty list to disable filtering entirely.
DEFAULT_WORKSPACE_DENYLIST = [".git", ".ssh"]

WorkspaceEntryKind = Literal["file", "dir", "symlink"]


SENTINEL = "__WP_WORKSPACE_BEGIN__"


class WorkspacePathError(ValueError):
    pass


class WorkspaceFileTooLargeError(ValueError):
    pass


class WorkspaceEntry(TypedDict):
    name: str
    kind: WorkspaceEntryKind
    size: int
    mtime: float


class DirListing(TypedDict):
    entries: list[WorkspaceEntry]
    truncated: bool
    overflow: int | None
    path: str


class WalkResult(TypedDict):
    paths: list[str]
    truncated: bool


class ResolvedPath(TypedDict):
    path: str
    # ``None`` when resolved without requiring the path to exist.
    kind: Literal["file", "dir"] | None


class FileContent(TypedDict):
    path: str
    size: int
    mtime: float
    encoding: str
    truncated: bool
    binary: bool
    content: str | None


def resolve_in_base(base: Path, rel: str, follow_symlinks: bool = False) -> Path:
    base_expanded = base.expanduser()
    base_resolved = base_expanded.resolve()
    target = base_expanded / rel
    lexical_target = Path(os.path.normpath(base_resolved / rel))
    resolved = Path(os.path.realpath(os.path.normpath(target)))
    if not _is_within(resolved, base_resolved):
        raise WorkspacePathError("path escapes workspace")
    if not follow_symlinks and _has_symlink_component(base_resolved, lexical_target):
        raise WorkspacePathError("symlink paths are not allowed")
    return resolved


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def is_denied(
    name_or_path: str | Path,
    denylist: list[str] | None = None,
) -> bool:
    patterns = DEFAULT_WORKSPACE_DENYLIST if denylist is None else denylist
    deny_patterns = [pattern.lower() for pattern in patterns]
    if not deny_patterns:
        return False
    path = Path(name_or_path)
    parts = [part for part in path.parts if part not in {"", "."}]
    if not parts:
        return False
    for part in parts:
        if any(fnmatch.fnmatch(part.lower(), pattern) for pattern in deny_patterns):
            return True
    normalized = path.as_posix().lower()
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in deny_patterns)


def relative_to_base(base: Path, resolved: Path) -> str:
    relative = resolved.relative_to(base.expanduser().resolve())
    return "" if relative == Path(".") else relative.as_posix()


def read_text_prefix(path: Path, max_bytes: int) -> tuple[str | None, bool, bool]:
    """Read at most ``max_bytes`` of UTF-8 text from the head of ``path``.

    Returns ``(content, truncated, binary)``, reading one byte past the ceiling
    to detect truncation and trimming a split multi-byte character from the
    tail.
    """
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    if b"\x00" in data:
        return None, truncated, True
    for trim in range(min(3, len(data)) + 1):
        kept = data[: len(data) - trim]
        try:
            return kept.decode("utf-8"), truncated, False
        except UnicodeDecodeError:
            # Only a split trailing character is recoverable.
            if not truncated:
                break
    return None, truncated, True


def read_text_capped(path: Path, max_bytes: int) -> tuple[str | None, bool, bool, str]:
    # An over-limit file yields no content (the workspace endpoint shows a size
    # notice); a truncated file is never reported as binary.
    content, truncated, binary = read_text_prefix(path, max_bytes)
    if truncated:
        return None, True, False, "utf-8"
    if binary:
        return None, False, True, "utf-8"
    return content, False, False, "utf-8"


def list_dir(
    base: Path,
    rel: str,
    cap: int,
    denylist: list[str] | None = None,
    follow_symlinks: bool = False,
    offset: int = 0,
) -> tuple[list[WorkspaceEntry], bool, int | None, Path]:
    if is_denied(rel, denylist):
        raise WorkspacePathError("path is denied")
    directory = resolve_in_base(base, rel, follow_symlinks=follow_symlinks)
    if not directory.exists():
        raise FileNotFoundError(directory)
    if not directory.is_dir():
        raise NotADirectoryError(directory)

    base_resolved = base.expanduser().resolve()
    allowed: list[WorkspaceEntry] = []
    for child in directory.iterdir():
        child_rel = child.relative_to(base_resolved)
        if is_denied(child_rel, denylist):
            continue
        stat = child.lstat()
        allowed.append(
            {
                "name": child.name,
                "kind": _entry_kind(child),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    allowed.sort(key=lambda entry: (entry["kind"] != "dir", entry["name"].lower()))
    if cap < 0:
        cap = 0
    if offset < 0:
        offset = 0
    # The sort is stable, so paging by offset over an unchanged directory is
    # deterministic; ``overflow`` counts entries past this page so the caller can
    # request the next one.
    page = allowed[offset : offset + cap]
    overflow = max(len(allowed) - (offset + cap), 0)
    return page, overflow > 0, overflow or None, directory


def _entry_kind(path: Path) -> WorkspaceEntryKind:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "dir"
    return "file"


def _has_symlink_component(base_resolved: Path, target: Path) -> bool:
    try:
        relative = target.relative_to(base_resolved)
    except ValueError:
        return False
    current = base_resolved
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


# Hard cap on entries visited by the fallback walk so a query never traverses an
# unbounded tree (e.g. a non-repo workspace with a huge node_modules).
WALK_VISIT_CAP = 20000


def walk_files(
    base: Path,
    denylist: list[str] | None = None,
    follow_symlinks: bool = False,
    visit_cap: int = WALK_VISIT_CAP,
) -> tuple[list[str], bool]:
    # Filesystem fallback for the file finder outside a git repo. Returns
    # ``base``-relative file paths and whether the visit cap was hit. Denied
    # directories are pruned so their subtrees are never descended.
    base_resolved = base.expanduser().resolve()
    out: list[str] = []
    visited = 0
    for root, dirs, files in os.walk(base_resolved, followlinks=follow_symlinks):
        root_path = Path(root)
        kept: list[str] = []
        for name in dirs:
            child = root_path / name
            rel = child.relative_to(base_resolved)
            if is_denied(rel, denylist):
                continue
            if not follow_symlinks and child.is_symlink():
                continue
            kept.append(name)
        dirs[:] = kept
        for name in files:
            visited += 1
            if visited > visit_cap:
                return out, True
            child = root_path / name
            if not follow_symlinks and child.is_symlink():
                continue
            rel = child.relative_to(base_resolved)
            if is_denied(rel, denylist):
                continue
            out.append(rel.as_posix())
    return out, False


def _subsequence_score(query: str, path: str) -> int | None:
    # Case-insensitive subsequence match with bonuses for word-boundary and
    # consecutive hits, and for matches landing in the basename. Returns ``None``
    # when ``query`` is not a subsequence of ``path``.
    if not query:
        return 0
    lowered = path.lower()
    needle = query.lower()
    score = 0
    qi = 0
    prev = -2
    for pi, ch in enumerate(lowered):
        if qi >= len(needle) or ch != needle[qi]:
            continue
        score += 1
        if pi == 0 or not lowered[pi - 1].isalnum():
            score += 10
        if pi == prev + 1:
            score += 5
        prev = pi
        qi += 1
    if qi != len(needle):
        return None
    basename = lowered.rsplit("/", 1)[-1]
    if needle in basename:
        score += 15
    score -= len(lowered) // 40  # shorter paths edge ahead on ties
    return score


def rank_files(
    query: str,
    paths: list[str],
    denylist: list[str] | None = None,
    limit: int = 50,
) -> tuple[list[str], bool]:
    # Score, filter, and order candidate paths for the finder. Returns the top
    # ``limit`` matches and whether more matched than were returned. An empty
    # query matches nothing (callers should not search on a blank input).
    if not query:
        return [], False
    scored: list[tuple[int, int, str]] = []
    for path in paths:
        if is_denied(path, denylist):
            continue
        result = _subsequence_score(query, path)
        if result is None:
            continue
        scored.append((result, len(path), path))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    truncated = len(scored) > limit
    return [path for _, _, path in scored[:limit]], truncated


def resolve_allowed(
    base: Path, rel: str, denylist: list[str] | None, follow_symlinks: bool
) -> Path:
    if is_denied(rel, denylist):
        raise WorkspacePathError("path is denied")
    return resolve_in_base(base, rel, follow_symlinks=follow_symlinks)


def resolve_existing_file(
    base: Path, rel: str, denylist: list[str] | None, follow_symlinks: bool
) -> Path:
    resolved = resolve_allowed(base, rel, denylist, follow_symlinks)
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved


def run_git_commands(base: str, commands: list[list[str]]) -> list[tuple[int, bytes]]:
    # ``git -C`` does no tilde expansion.
    cwd = os.path.expanduser(base)
    results: list[tuple[int, bytes]] = []
    for args in commands:
        try:
            completed = subprocess.run(
                ["git", "-C", cwd, *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except FileNotFoundError:
            results.append((127, b""))
            continue
        results.append((completed.returncode, completed.stdout))
    return results


def op_list_dir(
    base: str,
    rel: str,
    cap: int,
    offset: int,
    denylist: list[str] | None,
    follow_symlinks: bool,
) -> DirListing:
    base_path = Path(base)
    entries, truncated, overflow, directory = list_dir(
        base_path, rel, cap, denylist, follow_symlinks, offset
    )
    return {
        "entries": entries,
        "truncated": truncated,
        "overflow": overflow,
        "path": relative_to_base(base_path, directory),
    }


def op_walk_files(
    base: str, denylist: list[str] | None, follow_symlinks: bool
) -> WalkResult:
    paths, truncated = walk_files(Path(base), denylist, follow_symlinks)
    return {"paths": paths, "truncated": truncated}


def op_resolve(
    base: str,
    rel: str,
    denylist: list[str] | None,
    follow_symlinks: bool,
    must_exist: bool,
) -> ResolvedPath:
    base_path = Path(base)
    resolved = resolve_allowed(base_path, rel, denylist, follow_symlinks)
    path = relative_to_base(base_path, resolved)
    if not must_exist:
        return {"path": path, "kind": None}
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    return {"path": path, "kind": "dir" if resolved.is_dir() else "file"}


def op_read_file(
    base: str,
    rel: str,
    max_bytes: int,
    denylist: list[str] | None,
    follow_symlinks: bool,
) -> FileContent:
    base_path = Path(base)
    resolved = resolve_existing_file(base_path, rel, denylist, follow_symlinks)
    stat = resolved.stat()
    content, truncated, binary, encoding = read_text_capped(resolved, max_bytes)
    return {
        "path": relative_to_base(base_path, resolved),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "encoding": encoding,
        "truncated": truncated,
        "binary": binary,
        "content": content,
    }


def op_read_raw(
    base: str,
    rel: str,
    max_bytes: int,
    denylist: list[str] | None,
    follow_symlinks: bool,
) -> dict[str, str]:
    resolved = resolve_existing_file(Path(base), rel, denylist, follow_symlinks)
    if resolved.stat().st_size > max_bytes:
        raise WorkspaceFileTooLargeError(str(resolved))
    return {
        "name": resolved.name,
        "data_b64": base64.b64encode(resolved.read_bytes()).decode("ascii"),
    }


def op_git(base: str, commands: list[list[str]]) -> dict[str, list[list[Any]]]:
    return {
        "results": [
            [code, base64.b64encode(out).decode("ascii")]
            for code, out in run_git_commands(base, commands)
        ]
    }


def op_list_dirs(
    prefix: str, limit: int, denylist: list[str] | None
) -> dict[str, list[str]]:
    # Completes a typed absolute or ``~``-relative directory path to its child
    # directories, keeping the typed form (a leading ``~`` stays unexpanded).
    if prefix == "~":
        prefix = "~/"
    if not prefix.startswith(("/", "~/")):
        return {"directories": []}
    parent_typed, partial = prefix.rsplit("/", 1)
    parent_typed += "/"
    if is_denied(parent_typed, denylist):
        return {"directories": []}
    show_hidden = partial.startswith(".")
    names: list[str] = []
    try:
        with os.scandir(os.path.expanduser(parent_typed)) as entries:
            for entry in entries:
                name = entry.name
                if not name.startswith(partial):
                    continue
                if name.startswith(".") and not show_hidden:
                    continue
                if is_denied(name, denylist):
                    continue
                try:
                    if entry.is_dir():
                        names.append(name)
                except OSError:
                    continue
    except OSError:
        return {"directories": []}
    names.sort(key=lambda name: (name.lower(), name))
    return {"directories": [parent_typed + name for name in names[:limit]]}


OPS: dict[str, Callable[..., Any]] = {
    "list_dir": op_list_dir,
    "walk_files": op_walk_files,
    "resolve": op_resolve,
    "read_file": op_read_file,
    "read_raw": op_read_raw,
    "git": op_git,
    "list_dirs": op_list_dirs,
}

ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (WorkspacePathError, "denied"),
    (WorkspaceFileTooLargeError, "too_large"),
    (NotADirectoryError, "not_a_directory"),
    (FileNotFoundError, "not_found"),
)


def _main(argv: list[str]) -> None:
    try:
        payload = OPS[argv[1]](**json.loads(argv[2]))
    except Exception as exc:
        code = next((c for cls, c in ERROR_CODES if isinstance(exc, cls)), "failed")
        payload = {"error": code, "detail": str(exc)[:240]}
    sys.stdout.write(SENTINEL + json.dumps(payload) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    _main(sys.argv)
