"""Claude Code stream-json → canonical-event normalisation helpers.

Pure functions extracted from the Claude CLI adapter so the wire-shape
contract (status events, compact boundaries, rate-limit messages,
approval prompts, content-block normalisation) is testable without
spinning up the streaming pipeline. The adapter still owns the
session-state mutations (terminal fragments, streamed_tool_result_ids,
pending control requests) — those aren't normalisation, they're per
session bookkeeping.
"""

import json
from typing import Any

from waypoint.schemas import SessionStatus


def format_status_event(event: dict[str, Any]) -> tuple[str, SessionStatus]:
    status_label = event.get("status")
    compact_result = event.get("compact_result")
    if status_label == "compacting":
        return "Compacting context…", SessionStatus.RUNNING
    if compact_result is not None:
        return (
            f"Context compaction {compact_result}",
            (
                SessionStatus.IDLE
                if compact_result == "success"
                else SessionStatus.ERROR
            ),
        )
    return "", SessionStatus.RUNNING


def format_compact_boundary(metadata: dict[str, Any]) -> str:
    pre = metadata.get("pre_tokens")
    post = metadata.get("post_tokens")
    duration_ms = metadata.get("duration_ms")
    trigger = metadata.get("trigger") or "manual"
    parts = [f"Context compacted ({trigger})"]
    if pre is not None and post is not None:
        parts.append(f"{pre} → {post} tokens")
    if duration_ms is not None:
        parts.append(f"{duration_ms} ms")
    return " · ".join(parts)


def format_rate_limit(info: dict[str, Any]) -> str:
    status = info.get("status", "unknown")
    rl_type = info.get("rate_limit_type", "")
    return f"Rate limit ({rl_type}): {status}".strip()


def format_approval_text(payload: dict[str, Any]) -> str:
    tool_name = payload.get("tool_name") or "tool"
    tool_input = payload.get("tool_input") or {}
    if tool_name == "Bash":
        command = tool_input.get("command") or ""
        return f"Approve Bash command:\n{command}"
    if tool_name in {"Edit", "Write", "MultiEdit"}:
        path = tool_input.get("file_path") or tool_input.get("path") or ""
        return f"Approve {tool_name} on {path}"
    if tool_name == "ExitPlanMode":
        # Plan text is already rendered as a markdown agent_output
        # above this card — keep this prompt compact to avoid
        # duplication.
        return "Approve plan and exit plan mode"
    if tool_name in {"Task", "Agent"}:
        # The prompt body can be many kilobytes; the frontend
        # renders it as markdown from metadata.tool_input.prompt
        # instead of dumping JSON here.
        description = str(tool_input.get("description") or "").strip()
        subagent = str(tool_input.get("subagent_type") or "").strip()
        label = description or "subagent task"
        if subagent:
            label = f"{label} (via {subagent})"
        return f"Approve subagent task: {label}"
    if tool_name == "WebFetch":
        url = str(tool_input.get("url") or "").strip()
        return f"Approve WebFetch: {url}" if url else "Approve WebFetch"
    if tool_name == "WebSearch":
        query = str(tool_input.get("query") or "").strip()
        return f"Approve WebSearch: {query}" if query else "Approve WebSearch"
    if tool_name == "NotebookEdit":
        path = str(tool_input.get("notebook_path") or "").strip()
        return f"Approve NotebookEdit on {path}" if path else "Approve NotebookEdit"
    return f"Approve {tool_name}: {json.dumps(tool_input)[:240]}"


def iter_content_blocks(content: Any) -> list[dict[str, Any]]:
    # Claude Code normally streams message content as a list of typed
    # blocks, but synthetic turns (notably the user echo after
    # /compact) can arrive as a bare string or a list mixing strings
    # and dicts. Coerce everything to a list of dicts so callers can
    # rely on .get().
    if not content:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    blocks: list[dict[str, Any]] = []
    for entry in content:
        if isinstance(entry, dict):
            blocks.append(entry)
        elif isinstance(entry, str):
            blocks.append({"type": "text", "text": entry})
    return blocks


def stringify_tool_result(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for entry in content:
            if isinstance(entry, dict):
                if entry.get("type") == "text" and isinstance(
                    entry.get("text"), str
                ):
                    parts.append(entry["text"])
                elif "text" in entry:
                    parts.append(str(entry["text"]))
                else:
                    parts.append(json.dumps(entry))
            else:
                parts.append(str(entry))
        return "\n".join(parts)
    return json.dumps(content)
