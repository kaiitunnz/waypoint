import difflib
from typing import Any, Literal

from pydantic import BaseModel, Field

DiffPhase = Literal["proposed", "applied", "aggregate"]
ChangeType = Literal["add", "delete", "update", "move", "unknown"]

DEFAULT_MAX_FILE_BYTES = 200_000
DEFAULT_MAX_TOTAL_BYTES = 1_000_000


class DiffPreviewFile(BaseModel):
    path: str
    old_path: str | None = None
    change_type: ChangeType = "unknown"
    diff: str = ""
    additions: int = 0
    deletions: int = 0
    truncated: bool = False
    binary: bool = False
    unavailable_reason: str | None = None


class DiffPreviewPayload(BaseModel):
    schema_version: Literal[1] = 1
    phase: DiffPhase
    files: list[DiffPreviewFile] = Field(default_factory=list)
    total_additions: int = 0
    total_deletions: int = 0
    truncated: bool = False


def build_preview(
    phase: DiffPhase,
    files: list[DiffPreviewFile],
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> DiffPreviewPayload | None:
    limited_files: list[DiffPreviewFile] = []
    total_bytes = 0
    truncated = False
    for file in files:
        limited = _limit_file(file, max_file_bytes)
        encoded_len = len(limited.diff.encode("utf-8"))
        if total_bytes + encoded_len > max_total_bytes:
            remaining = max(max_total_bytes - total_bytes, 0)
            limited = _limit_file(limited, remaining)
            truncated = True
        total_bytes += len(limited.diff.encode("utf-8"))
        truncated = truncated or limited.truncated
        limited_files.append(limited)
        if total_bytes >= max_total_bytes:
            break
    if not limited_files:
        return None
    return DiffPreviewPayload(
        phase=phase,
        files=limited_files,
        total_additions=sum(file.additions for file in limited_files),
        total_deletions=sum(file.deletions for file in limited_files),
        truncated=truncated,
    )


def file_from_unified_diff(
    path: str,
    diff: str,
    change_type: ChangeType = "unknown",
    old_path: str | None = None,
    additions: int | None = None,
    deletions: int | None = None,
    binary: bool = False,
    unavailable_reason: str | None = None,
) -> DiffPreviewFile:
    counted_additions, counted_deletions = count_unified_diff(diff)
    return DiffPreviewFile(
        path=path,
        old_path=old_path,
        change_type=change_type,
        diff=diff,
        additions=counted_additions if additions is None else additions,
        deletions=counted_deletions if deletions is None else deletions,
        binary=binary,
        unavailable_reason=unavailable_reason,
    )


def file_from_old_new(
    path: str,
    old: str,
    new: str,
    change_type: ChangeType = "update",
    old_path: str | None = None,
) -> DiffPreviewFile:
    fromfile = old_path or path
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=path,
        )
    )
    if diff and not diff.endswith("\n"):
        diff += "\n"
    return file_from_unified_diff(path, diff, change_type, old_path=old_path)


def unavailable_file(
    path: str,
    reason: str,
    change_type: ChangeType = "unknown",
    old_path: str | None = None,
) -> DiffPreviewFile:
    return DiffPreviewFile(
        path=path,
        old_path=old_path,
        change_type=change_type,
        unavailable_reason=reason,
    )


def files_from_unified_diff(
    diff: str, fallback_path: str = "changes"
) -> list[DiffPreviewFile]:
    chunks = _split_unified_diff(diff)
    if not chunks:
        return [file_from_unified_diff(fallback_path, diff)] if diff else []
    files: list[DiffPreviewFile] = []
    for chunk in chunks:
        path, old_path = _paths_from_diff_chunk(chunk, fallback_path)
        files.append(
            file_from_unified_diff(
                path=path,
                old_path=old_path if old_path != path else None,
                change_type=_change_type_from_chunk(chunk),
                diff="\n".join(chunk) + "\n",
            )
        )
    return files


def files_from_codex_file_changes(changes: Any) -> list[DiffPreviewFile]:
    if not isinstance(changes, list):
        return []
    files: list[DiffPreviewFile] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        path = str(change.get("path") or "").strip()
        diff = change.get("diff")
        if not path or not isinstance(diff, str):
            continue
        kind_raw = change.get("kind")
        kind = _normalize_change_type(kind_raw)
        if kind == "add":
            files.append(file_from_old_new(path, "", diff, "add"))
        elif kind == "delete":
            files.append(file_from_old_new(path, diff, "", "delete"))
        else:
            move_path = _move_path_from_change_kind(kind_raw)
            files.append(
                file_from_unified_diff(
                    str(move_path or path),
                    diff,
                    "move" if move_path else kind,
                    old_path=path if move_path else None,
                )
            )
    return files


def files_from_codex_legacy_file_changes(file_changes: Any) -> list[DiffPreviewFile]:
    if not isinstance(file_changes, dict):
        return []
    files: list[DiffPreviewFile] = []
    for raw_path, change in file_changes.items():
        path = str(raw_path)
        if not isinstance(change, dict):
            continue
        change_type = change.get("type")
        if change_type == "update":
            diff = change.get("unified_diff")
            if isinstance(diff, str):
                move_path = change.get("move_path")
                path_out = (
                    str(move_path) if isinstance(move_path, str) and move_path else path
                )
                files.append(
                    file_from_unified_diff(
                        path_out,
                        diff,
                        "move" if move_path else "update",
                        old_path=path if move_path else None,
                    )
                )
        elif change_type == "add":
            content = change.get("content")
            if isinstance(content, str):
                files.append(file_from_old_new(path, "", content, "add"))
        elif change_type == "delete":
            content = change.get("content")
            if isinstance(content, str):
                files.append(file_from_old_new(path, content, "", "delete"))
    return files


def files_from_opencode_diffs(diffs: Any) -> list[DiffPreviewFile]:
    if not isinstance(diffs, list):
        return []
    files: list[DiffPreviewFile] = []
    for entry in diffs:
        if not isinstance(entry, dict):
            continue
        path = str(
            entry.get("path")
            or entry.get("file")
            or entry.get("name")
            or entry.get("filename")
            or "changes"
        )
        old_path = entry.get("old_path") or entry.get("oldPath")
        diff = entry.get("diff") or entry.get("patch")
        before = entry.get("before")
        after = entry.get("after")
        additions = entry.get("additions")
        deletions = entry.get("deletions")
        change_type = _normalize_change_type(
            entry.get("type") or entry.get("kind") or entry.get("status")
        )
        if isinstance(diff, str) and diff:
            files.append(
                file_from_unified_diff(
                    path=path,
                    diff=diff,
                    change_type=change_type,
                    old_path=str(old_path) if isinstance(old_path, str) else None,
                    additions=additions if isinstance(additions, int) else None,
                    deletions=deletions if isinstance(deletions, int) else None,
                )
            )
        elif isinstance(before, str) and isinstance(after, str):
            file = file_from_old_new(
                path=path,
                old=before,
                new=after,
                change_type=change_type,
                old_path=str(old_path) if isinstance(old_path, str) else None,
            )
            files.append(
                file.model_copy(
                    update={
                        "additions": (
                            additions if isinstance(additions, int) else file.additions
                        ),
                        "deletions": (
                            deletions if isinstance(deletions, int) else file.deletions
                        ),
                    }
                )
            )
        elif isinstance(additions, int) or isinstance(deletions, int):
            files.append(
                DiffPreviewFile(
                    path=path,
                    old_path=str(old_path) if isinstance(old_path, str) else None,
                    change_type=change_type,
                    additions=additions if isinstance(additions, int) else 0,
                    deletions=deletions if isinstance(deletions, int) else 0,
                    unavailable_reason="diff content was not included by backend",
                )
            )
    return files


def preview_to_metadata(preview: DiffPreviewPayload | None) -> dict[str, Any]:
    if preview is None:
        return {}
    return {"diff_preview": preview.model_dump(mode="json")}


def count_unified_diff(diff: str) -> tuple[int, int]:
    additions = 0
    deletions = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return additions, deletions


def _normalize_change_type(value: Any) -> ChangeType:
    if isinstance(value, dict):
        value = value.get("type")
    normalized = str(value or "").lower()
    if normalized in {"add", "added", "create", "created", "new"}:
        return "add"
    if normalized in {"delete", "deleted", "remove", "removed"}:
        return "delete"
    if normalized in {"update", "updated", "modify", "modified", "change"}:
        return "update"
    if normalized in {"move", "moved", "rename", "renamed"}:
        return "move"
    return "unknown"


def _move_path_from_change_kind(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    if value.get("type") != "update":
        return None
    move_path = value.get("move_path")
    return move_path if isinstance(move_path, str) and move_path else None


def _split_unified_diff(diff: str) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git ") and current:
            chunks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append(current)
    return chunks


def _paths_from_diff_chunk(
    lines: list[str], fallback_path: str
) -> tuple[str, str | None]:
    old_path: str | None = None
    new_path: str | None = None
    for line in lines:
        if line.startswith("--- "):
            old_path = _clean_diff_path(line[4:].strip())
        elif line.startswith("+++ "):
            new_path = _clean_diff_path(line[4:].strip())
    if new_path and new_path != "/dev/null":
        return new_path, old_path if old_path != "/dev/null" else None
    if old_path and old_path != "/dev/null":
        return old_path, old_path
    for line in lines:
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                return _clean_diff_path(parts[3]), _clean_diff_path(parts[2])
    return fallback_path, None


def _clean_diff_path(path: str) -> str:
    path = path.split("\t", 1)[0]
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _change_type_from_chunk(lines: list[str]) -> ChangeType:
    old_path = None
    new_path = None
    for line in lines:
        if line.startswith("--- "):
            old_path = _clean_diff_path(line[4:].strip())
        elif line.startswith("+++ "):
            new_path = _clean_diff_path(line[4:].strip())
    if old_path == "/dev/null":
        return "add"
    if new_path == "/dev/null":
        return "delete"
    if old_path and new_path and old_path != new_path:
        return "move"
    return "update"


def _limit_file(file: DiffPreviewFile, max_bytes: int) -> DiffPreviewFile:
    if max_bytes <= 0:
        return file.model_copy(update={"diff": "", "truncated": True})
    encoded = file.diff.encode("utf-8")
    if len(encoded) <= max_bytes:
        return file
    limited = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return file.model_copy(update={"diff": limited, "truncated": True})
