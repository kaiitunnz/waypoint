import fnmatch
import os
from pathlib import Path
from typing import Literal, TypedDict

DEFAULT_WORKSPACE_DENYLIST = [".git", ".claude", ".env", ".ssh"]

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
    deny_patterns = denylist or DEFAULT_WORKSPACE_DENYLIST
    path = Path(name_or_path)
    parts = [part for part in path.parts if part not in {"", "."}]
    for part in parts:
        if part.startswith("."):
            return True
        if any(fnmatch.fnmatch(part, pattern) for pattern in deny_patterns):
            return True
    normalized = path.as_posix()
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
    overflow = max(len(allowed) - cap, 0)
    return allowed[:cap], overflow > 0, overflow or None, directory


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
