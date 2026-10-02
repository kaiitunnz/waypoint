"""Disposition of every Codex thread item type and notification method.

``ITEMS`` maps a thread item ``type`` to the renderers for its started,
updated, and completed notifications. ``NOTIFICATIONS`` maps a notification
method to a handler or an ``Ignored`` marker. Lookups that miss either table
take the fallback path, which only a CLI newer than the pinned SDK reaches.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from waypoint.backends.codex.normalize import (
    error_text,
    extract_item,
    format_plan,
    format_todo_list,
    is_retryable_error,
    map_turn_status,
)
from waypoint.backends.codex.questions import async_questions, is_async_message
from waypoint.backends.diff_preview import build_preview, files_from_unified_diff
from waypoint.schemas import EventKind, SessionStatus


@dataclass(frozen=True)
class Rendered:
    kind: EventKind
    text: str
    status: SessionStatus = SessionStatus.RUNNING
    # Merged into the event's metadata after the adapter's envelope keys.
    metadata: dict[str, Any] = field(default_factory=dict)

    def triple(self) -> tuple[EventKind, str, SessionStatus]:
        return self.kind, self.text, self.status


ItemRenderer = Callable[[dict[str, Any]], Rendered | None]
NotificationHandler = Callable[[dict[str, Any]], Rendered | None]


@dataclass(frozen=True)
class ItemSpec:
    tool_name: Callable[[dict[str, Any]], str | None] | None = None
    started: ItemRenderer | None = None
    updated: ItemRenderer | None = None
    completed: ItemRenderer | None = None


@dataclass(frozen=True)
class Ignored:
    reason: str


NotificationSpec = NotificationHandler | Ignored


# ─── Item renderers ───


def _tool_call(text: str) -> Rendered:
    return Rendered(EventKind.TOOL_CALL, text)


def _tool_result(text: str) -> Rendered:
    return Rendered(EventKind.TOOL_RESULT, text)


def _joined_paths(changes: Any) -> str:
    if not isinstance(changes, list):
        return ""
    return ", ".join(
        str(change.get("path", "")) for change in changes if isinstance(change, dict)
    )


def _namespaced(item: dict[str, Any]) -> str:
    tool = item.get("tool", "")
    namespace = item.get("namespace", "")
    return f"{namespace}:{tool}" if namespace else str(tool)


def _collab_agent_messages(item: dict[str, Any]) -> list[str]:
    """Subagent report bodies carried by a completed collab-agent tool item.

    Codex collaboration mode records each waited-on subagent's reply under
    ``agentsStates[threadId].message``.
    """
    states = item.get("agentsStates")
    if not isinstance(states, dict):
        return []
    messages: list[str] = []
    for state in states.values():
        if isinstance(state, dict):
            message = state.get("message")
            if isinstance(message, str) and message.strip():
                messages.append(message.strip())
    return messages


def _format_collab_agent_tool(item: dict[str, Any], *, completed: bool) -> str:
    """Text for a collab-agent tool item: the spawn prompt on the call, the
    subagent reports on the result, falling back to the bare tool name."""
    tool = str(item.get("tool") or "") or "collab agent tool call"
    if completed:
        bodies = _collab_agent_messages(item)
    else:
        prompt = item.get("prompt")
        bodies = [prompt.strip()] if isinstance(prompt, str) and prompt.strip() else []
    if not bodies:
        return tool
    return f"{tool}\n\n" + "\n\n".join(bodies)


def _command_completed(item: dict[str, Any]) -> Rendered:
    output = item.get("aggregatedOutput") or ""
    suffix = f"\n{output}" if output else ""
    return _tool_result(f"$ {item.get('command', '')}{suffix}")


def _mcp_tool_name(item: dict[str, Any]) -> str | None:
    server = item.get("server", "")
    tool = item.get("tool", "")
    if server and tool:
        return f"{server}:{tool}"
    return str(tool or server) or None


def _namespaced_tool_name(item: dict[str, Any]) -> str | None:
    return _namespaced(item) or None


def _agent_message_started(item: dict[str, Any]) -> Rendered | None:
    if is_async_message(item):
        # An async message arrives whole; it is emitted once, on completion.
        return None
    return Rendered(EventKind.AGENT_OUTPUT, item.get("text", ""))


def _agent_message_completed(item: dict[str, Any]) -> Rendered | None:
    if not is_async_message(item):
        # The streamed deltas already carried the text.
        return None
    if async_questions(item):
        return _tool_call("Need your input")
    return Rendered(EventKind.AGENT_OUTPUT, item.get("text", ""))


ITEMS: dict[str, ItemSpec] = {
    "commandExecution": ItemSpec(
        tool_name=lambda item: "Bash",
        started=lambda item: _tool_call(f"$ {item.get('command', '')}"),
        completed=_command_completed,
    ),
    "fileChange": ItemSpec(
        tool_name=lambda item: "Edit",
        started=lambda item: _tool_call(
            f"Preparing file changes: {_joined_paths(item.get('changes'))}"
        ),
        completed=lambda item: _tool_result(
            f"File changes completed: {_joined_paths(item.get('changes'))}"
        ),
    ),
    "mcpToolCall": ItemSpec(
        tool_name=_mcp_tool_name,
        started=lambda item: _tool_call(
            f"MCP {item.get('server', '')}:{item.get('tool', '')}"
        ),
    ),
    "dynamicToolCall": ItemSpec(
        tool_name=_namespaced_tool_name,
        started=lambda item: _tool_call(_namespaced(item) or "dynamic tool call"),
        completed=lambda item: _tool_result(_namespaced(item) or "dynamic tool call"),
    ),
    "collabAgentToolCall": ItemSpec(
        tool_name=_namespaced_tool_name,
        started=lambda item: _tool_call(
            _format_collab_agent_tool(item, completed=False)
        ),
        completed=lambda item: _tool_result(
            _format_collab_agent_tool(item, completed=True)
        ),
    ),
    "webSearch": ItemSpec(
        tool_name=lambda item: "WebSearch",
        started=lambda item: _tool_call(str(item.get("query", "web search"))),
        completed=lambda item: _tool_result(str(item.get("query", "web search"))),
    ),
    "plan": ItemSpec(
        started=lambda item: Rendered(EventKind.SYSTEM_NOTE, item.get("text", "")),
        completed=lambda item: Rendered(EventKind.SYSTEM_NOTE, "Completed plan"),
    ),
    "agentMessage": ItemSpec(
        started=_agent_message_started,
        completed=_agent_message_completed,
    ),
    # Synthesized by the adapter from ``turn/plan/updated``.
    "todo_list": ItemSpec(
        started=lambda item: _tool_call(format_todo_list(item)),
        updated=lambda item: _tool_result(format_todo_list(item)),
        completed=lambda item: _tool_result(format_todo_list(item)),
    ),
}


def _fallback_started(item: dict[str, Any]) -> Rendered:
    return Rendered(EventKind.SYSTEM_NOTE, f"Started {item.get('type') or 'item'}")


def _fallback_updated(item: dict[str, Any]) -> Rendered:
    return Rendered(EventKind.SYSTEM_NOTE, f"Updated {item.get('type') or 'item'}")


def _fallback_completed(item: dict[str, Any]) -> Rendered:
    return Rendered(EventKind.SYSTEM_NOTE, f"Completed {item.get('type') or 'item'}")


_FALLBACK_ITEM = ItemSpec(
    started=_fallback_started,
    updated=_fallback_updated,
    completed=_fallback_completed,
)


def item_spec(item_type: Any) -> ItemSpec:
    spec = ITEMS.get(item_type) if isinstance(item_type, str) else None
    return spec if spec is not None else _FALLBACK_ITEM


def render_item_started(item: dict[str, Any]) -> Rendered | None:
    renderer = item_spec(item.get("type")).started
    return renderer(item) if renderer is not None else None


def render_item_updated(item: dict[str, Any]) -> Rendered | None:
    renderer = item_spec(item.get("type")).updated
    return renderer(item) if renderer is not None else None


def render_item_completed(item: dict[str, Any]) -> Rendered | None:
    # An item finishing isn't a turn finishing; renderers report RUNNING and
    # the transition to IDLE belongs to turn/completed.
    renderer = item_spec(item.get("type")).completed
    return renderer(item) if renderer is not None else None


def extract_tool_name(item_type: str | None, item: dict[str, Any]) -> str | None:
    """Canonical ``metadata["tool_name"]`` for a Codex item."""
    tool_name = item_spec(item_type).tool_name
    return tool_name(item) if tool_name is not None else None


# ─── Notification handlers ───


def _delta(kind: EventKind) -> NotificationHandler:
    return lambda payload: Rendered(kind, str(payload.get("delta", "")))


def _patch_updated(payload: dict[str, Any]) -> Rendered:
    changes = payload.get("changes", [])
    paths = ", ".join(
        str(change.get("path", ""))
        for change in changes
        if isinstance(change, dict) and change.get("path")
    )
    return _tool_result(f"File changes updated: {paths}".strip())


def _turn_diff_updated(payload: dict[str, Any]) -> Rendered:
    diff = payload.get("diff")
    additions = deletions = 0
    if isinstance(diff, str):
        preview = build_preview(
            "aggregate", files_from_unified_diff(diff, "Turn changes")
        )
        if preview is not None:
            additions = preview.total_additions
            deletions = preview.total_deletions
    return Rendered(EventKind.SYSTEM_NOTE, f"Turn changes: +{additions} -{deletions}")


def _turn_started(payload: dict[str, Any]) -> Rendered:
    turn = payload.get("turn", {})
    return Rendered(
        EventKind.SYSTEM_NOTE, f"Turn started: {turn.get('id', '')}".strip()
    )


def _turn_completed(payload: dict[str, Any]) -> Rendered:
    turn = payload.get("turn", {})
    return Rendered(
        EventKind.SYSTEM_NOTE,
        f"Turn {turn.get('status', 'completed')}",
        map_turn_status(turn.get("status")),
    )


def _error(payload: dict[str, Any]) -> Rendered:
    return Rendered(
        EventKind.SYSTEM_NOTE,
        error_text(payload),
        SessionStatus.RUNNING if is_retryable_error(payload) else SessionStatus.ERROR,
    )


NOTIFICATIONS: dict[str, NotificationSpec] = {
    "item/started": lambda payload: render_item_started(extract_item(payload)),
    "item/updated": lambda payload: render_item_updated(extract_item(payload)),
    "item/completed": lambda payload: render_item_completed(extract_item(payload)),
    "item/agentMessage/delta": _delta(EventKind.AGENT_OUTPUT),
    "item/commandExecution/outputDelta": _delta(EventKind.TOOL_RESULT),
    "item/fileChange/outputDelta": _delta(EventKind.TOOL_RESULT),
    "item/fileChange/patchUpdated": _patch_updated,
    "turn/diff/updated": _turn_diff_updated,
    "turn/started": _turn_started,
    "turn/completed": _turn_completed,
    "thread/compacted": lambda payload: Rendered(
        EventKind.SYSTEM_NOTE, "Codex thread compacted", SessionStatus.IDLE
    ),
    # Codex's update_plan tool, surfaced as a todo_list result so it renders in
    # the shared todo dock/card; the adapter synthesizes the todo_list item.
    "turn/plan/updated": lambda payload: _tool_result(
        format_plan(payload.get("plan", []))
    ),
    "error": _error,
}


def render_notification(method: str, payload: dict[str, Any]) -> Rendered | None:
    spec = NOTIFICATIONS.get(method)
    if spec is None or isinstance(spec, Ignored):
        return None
    return spec(payload)


def map_notification(
    method: str, payload: dict[str, Any]
) -> tuple[EventKind | None, str, SessionStatus]:
    """``render_notification`` as a ``(kind, text, status)`` triple."""
    rendered = render_notification(method, payload)
    if rendered is None:
        return None, "", SessionStatus.RUNNING
    return rendered.triple()
