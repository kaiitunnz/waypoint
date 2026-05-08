#!/usr/bin/env python3
"""Waypoint PreToolUse hook for Claude Code.

Reads the hook payload from stdin, posts it to the Waypoint backend, blocks
until the backend returns an approval decision, and emits the appropriate
hookSpecificOutput envelope on stdout. Stdlib-only so the script works in any
Python the user happens to have on PATH.
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MAX_DIFF_BYTES = 200_000


def emit(decision: str, reason: str) -> None:
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


def build_diff_preview(payload: dict) -> dict | None:
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if tool_name not in {"Edit", "Write", "MultiEdit"} or not isinstance(
        tool_input, dict
    ):
        return None
    path_text = tool_input.get("file_path") or tool_input.get("path")
    if not isinstance(path_text, str) or not path_text:
        return None
    path = resolve_tool_path(path_text, payload.get("cwd"))
    display_path = path_text
    try:
        old = path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception as exc:
        return preview_payload(
            [
                {
                    "path": display_path,
                    "change_type": "unknown",
                    "diff": "",
                    "additions": 0,
                    "deletions": 0,
                    "truncated": False,
                    "binary": False,
                    "unavailable_reason": f"could not read file before edit: {exc}",
                }
            ]
        )

    try:
        if tool_name == "Write":
            content = tool_input.get("content")
            if not isinstance(content, str):
                return None
            new = content
            change_type = "add" if not path.exists() else "update"
        elif tool_name == "Edit":
            new = apply_edit(old, tool_input)
            change_type = "update"
        else:
            new = old
            for edit in tool_input.get("edits") or []:
                if isinstance(edit, dict):
                    new = apply_edit(new, edit)
            change_type = "update"
    except ValueError as exc:
        return preview_payload(
            [
                {
                    "path": display_path,
                    "change_type": "unknown",
                    "diff": "",
                    "additions": 0,
                    "deletions": 0,
                    "truncated": False,
                    "binary": False,
                    "unavailable_reason": str(exc),
                }
            ]
        )

    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=display_path,
            tofile=display_path,
        )
    )
    additions, deletions = count_unified_diff(diff)
    diff, truncated = limit_text(diff, MAX_DIFF_BYTES)
    return preview_payload(
        [
            {
                "path": display_path,
                "change_type": change_type,
                "diff": diff,
                "additions": additions,
                "deletions": deletions,
                "truncated": truncated,
                "binary": False,
                "unavailable_reason": None,
            }
        ]
    )


def resolve_tool_path(path_text: str, cwd: object) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    if isinstance(cwd, str) and cwd:
        return Path(cwd).expanduser() / path
    return Path.cwd() / path


def apply_edit(content: str, edit: dict) -> str:
    old = edit.get("old_string")
    new = edit.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str):
        raise ValueError("edit payload did not include old_string/new_string")
    if old == "":
        raise ValueError("edit payload old_string was empty")
    count = -1 if bool(edit.get("replace_all", False)) else 1
    if old not in content:
        raise ValueError("old_string was not found in the current file")
    return content.replace(old, new, count)


def preview_payload(files: list[dict]) -> dict:
    total_additions = sum(file.get("additions", 0) for file in files)
    total_deletions = sum(file.get("deletions", 0) for file in files)
    return {
        "schema_version": 1,
        "phase": "proposed",
        "files": files,
        "total_additions": total_additions,
        "total_deletions": total_deletions,
        "truncated": any(bool(file.get("truncated")) for file in files),
    }


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


def limit_text(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        emit("ask", f"hook payload not JSON: {exc}")
        return 0

    backend_url = os.environ.get("WAYPOINT_HOOK_URL")
    secret = os.environ.get("WAYPOINT_HOOK_SECRET")
    waypoint_session_id = os.environ.get("WAYPOINT_SESSION_ID")
    if not backend_url or not secret or not waypoint_session_id:
        emit("ask", "Waypoint hook env not configured; deferring to default policy")
        return 0

    try:
        timeout = float(os.environ.get("WAYPOINT_HOOK_TIMEOUT", "300"))
    except ValueError:
        timeout = 300.0

    outbound = {
        "waypoint_session_id": waypoint_session_id,
        "claude_session_id": payload.get("session_id"),
        "tool_name": payload.get("tool_name"),
        "tool_input": payload.get("tool_input"),
        "tool_use_id": payload.get("tool_use_id"),
        "transcript_path": payload.get("transcript_path"),
        "permission_mode": payload.get("permission_mode"),
        "cwd": payload.get("cwd"),
    }
    diff_preview = build_diff_preview(payload)
    if diff_preview is not None:
        outbound["diff_preview"] = diff_preview
    body = json.dumps(outbound).encode("utf-8")
    request = urllib.request.Request(
        backend_url.rstrip("/") + "/api/internal/hooks/claude/approval",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Waypoint-Hook-Secret": secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            decision = json.loads(response.read().decode("utf-8"))
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        json.JSONDecodeError,
    ) as exc:
        emit("ask", f"Waypoint hook error: {exc}")
        return 0

    emit(
        str(decision.get("permissionDecision", "ask")),
        str(decision.get("permissionDecisionReason", "")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
