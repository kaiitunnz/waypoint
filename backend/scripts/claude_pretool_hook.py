#!/usr/bin/env python3
"""Waypoint PreToolUse hook for Claude Code.

Reads the hook payload from stdin, posts it to the Waypoint backend, blocks
until the backend returns an approval decision, and emits the appropriate
hookSpecificOutput envelope on stdout. Stdlib-only so the script works in any
Python the user happens to have on PATH.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


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

    body = json.dumps(
        {
            "waypoint_session_id": waypoint_session_id,
            "claude_session_id": payload.get("session_id"),
            "tool_name": payload.get("tool_name"),
            "tool_input": payload.get("tool_input"),
            "tool_use_id": payload.get("tool_use_id"),
            "transcript_path": payload.get("transcript_path"),
            "permission_mode": payload.get("permission_mode"),
            "cwd": payload.get("cwd"),
        }
    ).encode("utf-8")
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
