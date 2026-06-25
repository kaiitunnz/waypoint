import fnmatch
import os
from pathlib import Path
from typing import Literal, TypedDict

# The denylist is the single filter knob. By default only VCS internals and
# SSH key material are hidden; ordinary dotfiles (.env, .gitignore, …) preview
# fine. Add globs like ".*" to hide all dotfiles, or set the denylist to an
# explicit empty list to disable filtering entirely.
DEFAULT_WORKSPACE_DENYLIST = [".git", ".ssh"]

WorkspaceEntryKind = Literal["file", "dir", "symlink"]


class WorkspacePathError(ValueError):
    pass


class WorkspaceEntry(TypedDict):
    name: str
    kind: WorkspaceEntryKind
    size: int
    mtime: float


def resolve_in_base(base: Path, rel: str, follow_symlinks: bool = False) -> Path:
    base_expanded = base.expanduser()
    base_resolved = base_expanded.resolve()
    target = base_expanded / rel
    lexical_target = Path(os.path.normpath(base_resolved / rel))
    resolved = Path(os.path.realpath(os.path.normpath(target)))
    if not resolved.is_relative_to(base_resolved):
        raise WorkspacePathError("path escapes workspace")
    if not follow_symlinks and _has_symlink_component(base_resolved, lexical_target):
        raise WorkspacePathError("symlink paths are not allowed")
    return resolved


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


def sniff_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def read_text_capped(path: Path, max_bytes: int) -> tuple[str | None, bool, bool, str]:
    size = path.stat().st_size
    if size > max_bytes:
        return None, True, False, "utf-8"
    data = path.read_bytes()
    if not sniff_text(data):
        return None, False, True, "utf-8"
    return data.decode("utf-8"), False, False, "utf-8"


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
    # ``limit`` matches and whether more matched than were returned.
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
