"""Codex notification → canonical-event normalisation.

The Codex App Server SDK delivers JSON-RPC notifications that need to
land in Waypoint's `EventRecord` shape (kind / text / status /
metadata). The adapter previously did this work inline; pulling it out
into pure functions makes the wire-shape contract testable in
isolation and keeps the adapter focused on session state.

Each helper takes the raw notification payload (already coerced to a
dict) and returns the canonical `(kind, text, status)` triple. The
adapter wraps these triples in the persisted `EventRecord` along with
its own `metadata.method` / `metadata.payload` envelope keys.
"""

from dataclasses import asdict, is_dataclass
from typing import Any

from codex_app_server.models import UnknownNotification

from waypoint.schemas import EventKind, SessionStatus


def map_notification(
    method: str,
    payload: dict[str, Any],
) -> tuple[EventKind | None, str, SessionStatus]:
    """Map a Codex App Server notification to (kind, text, status)."""

    if method == "item/agentMessage/delta":
        return (
            EventKind.AGENT_OUTPUT,
            str(payload.get("delta", "")),
            SessionStatus.RUNNING,
        )
    if method == "item/commandExecution/outputDelta":
        return (
            EventKind.TOOL_RESULT,
            str(payload.get("delta", "")),
            SessionStatus.RUNNING,
        )
    if method == "item/fileChange/outputDelta":
        return (
            EventKind.TOOL_RESULT,
            str(payload.get("delta", "")),
            SessionStatus.RUNNING,
        )
    if method == "turn/started":
        turn = payload.get("turn", {})
        return (
            EventKind.SYSTEM_NOTE,
            f"Turn started: {turn.get('id', '')}".strip(),
            SessionStatus.RUNNING,
        )
    if method == "turn/completed":
        turn = payload.get("turn", {})
        status = map_turn_status(turn.get("status"))
        return (
            EventKind.SYSTEM_NOTE,
            f"Turn {turn.get('status', 'completed')}",
            status,
        )
    if method == "thread/compacted":
        return (
            EventKind.SYSTEM_NOTE,
            "Codex thread compacted",
            SessionStatus.IDLE,
        )
    if method == "item/started":
        item = extract_item(payload)
        return _format_item_started(item)
    if method == "item/updated":
        item = extract_item(payload)
        return _format_item_updated(item)
    if method == "item/completed":
        item = extract_item(payload)
        return _format_item_completed(item)
    if method == "turn/plan/updated":
        plan = payload.get("plan", [])
        text = "\n".join(
            f"- {entry.get('step', '')} [{entry.get('status', '')}]" for entry in plan
        )
        return EventKind.SYSTEM_NOTE, text, SessionStatus.RUNNING
    if method == "error":
        error = payload.get("error", {})
        return (
            EventKind.SYSTEM_NOTE,
            str(error.get("message", "Codex error")),
            SessionStatus.ERROR,
        )
    return None, "", SessionStatus.RUNNING


def _format_item_started(
    item: dict[str, Any],
) -> tuple[EventKind, str, SessionStatus]:
    item_type = item.get("type")
    if item_type == "commandExecution":
        return (
            EventKind.TOOL_CALL,
            f"$ {item.get('command', '')}",
            SessionStatus.RUNNING,
        )
    if item_type == "fileChange":
        paths = ", ".join(change.get("path", "") for change in item.get("changes", []))
        return (
            EventKind.TOOL_CALL,
            f"Preparing file changes: {paths}",
            SessionStatus.RUNNING,
        )
    if item_type == "mcpToolCall":
        return (
            EventKind.TOOL_CALL,
            f"MCP {item.get('server', '')}:{item.get('tool', '')}",
            SessionStatus.RUNNING,
        )
    if item_type == "plan":
        return EventKind.SYSTEM_NOTE, item.get("text", ""), SessionStatus.RUNNING
    if item_type == "agentMessage":
        return EventKind.AGENT_OUTPUT, item.get("text", ""), SessionStatus.RUNNING
    if item_type == "todo_list":
        return (
            EventKind.TOOL_CALL,
            format_todo_list(item),
            SessionStatus.RUNNING,
        )
    return (
        EventKind.SYSTEM_NOTE,
        f"Started {item_type or 'item'}",
        SessionStatus.RUNNING,
    )


def _format_item_updated(
    item: dict[str, Any],
) -> tuple[EventKind, str, SessionStatus]:
    item_type = item.get("type")
    if item_type == "todo_list":
        return (
            EventKind.TOOL_RESULT,
            format_todo_list(item),
            SessionStatus.RUNNING,
        )
    return (
        EventKind.SYSTEM_NOTE,
        f"Updated {item_type or 'item'}",
        SessionStatus.RUNNING,
    )


def _format_item_completed(
    item: dict[str, Any],
) -> tuple[EventKind | None, str, SessionStatus]:
    item_type = item.get("type")
    if item_type == "agentMessage":
        return None, "", SessionStatus.RUNNING
    # An item finishing isn't a turn finishing — the model usually has
    # more tool calls or assistant output to emit before turn/completed
    # lands. Always report RUNNING here; the session-level transition
    # to IDLE belongs to the turn/completed handler.
    if item_type == "commandExecution":
        output = item.get("aggregatedOutput") or ""
        suffix = f"\n{output}" if output else ""
        return (
            EventKind.TOOL_RESULT,
            f"$ {item.get('command', '')}{suffix}",
            SessionStatus.RUNNING,
        )
    if item_type == "fileChange":
        paths = ", ".join(change.get("path", "") for change in item.get("changes", []))
        return (
            EventKind.TOOL_RESULT,
            f"File changes completed: {paths}",
            SessionStatus.RUNNING,
        )
    if item_type == "todo_list":
        return (
            EventKind.TOOL_RESULT,
            format_todo_list(item),
            SessionStatus.RUNNING,
        )
    return (
        EventKind.SYSTEM_NOTE,
        f"Completed {item_type or 'item'}",
        SessionStatus.RUNNING,
    )


def extract_item_id(payload: dict[str, Any]) -> str | None:
    candidate = payload.get("itemId")
    if isinstance(candidate, str) and candidate:
        return candidate
    item = extract_item(payload) if "item" in payload else None
    if isinstance(item, dict):
        inner = item.get("id")
        if isinstance(inner, str) and inner:
            return inner
    return None


def extract_item(payload: dict[str, Any]) -> dict[str, Any]:
    item = payload.get("item", {})
    if isinstance(item, dict) and len(item) == 1 and "root" in item:
        root = item["root"]
        if isinstance(root, dict):
            return root
    return item if isinstance(item, dict) else {}


def format_todo_list(item: dict[str, Any]) -> str:
    entries = item.get("items", [])
    if not isinstance(entries, list) or not entries:
        return "Todo list"
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        marker = "[x]" if entry.get("completed") else "[ ]"
        lines.append(f"{marker} {text}")
    return "\n".join(lines) if lines else "Todo list"


def payload_to_dict(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        dumped = payload.model_dump(mode="json", by_alias=True)
        return dumped if isinstance(dumped, dict) else {"value": dumped}
    if is_dataclass(payload) and not isinstance(payload, type):
        dumped = asdict(payload)
        return dumped if isinstance(dumped, dict) else {"value": dumped}
    if isinstance(payload, UnknownNotification):
        return payload.params
    if isinstance(payload, dict):
        return payload
    return {"value": str(payload)}


def map_turn_status(value: Any) -> SessionStatus:
    if value == "completed":
        return SessionStatus.IDLE
    if value == "interrupted":
        return SessionStatus.INTERRUPTED
    if value == "failed":
        return SessionStatus.ERROR
    return SessionStatus.RUNNING


def format_approval_text(method: str, params: dict[str, Any]) -> str:
    if method == "item/commandExecution/requestApproval":
        return f"Approve command: {params.get('command', '')}"
    if method == "item/fileChange/requestApproval":
        return "Approve file changes"
    return f"Approve request: {method}"
