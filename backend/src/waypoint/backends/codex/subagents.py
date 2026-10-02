"""Codex ``subAgentActivity`` items as subagent tool entries and task cards.

A Codex subagent runs in its own thread. The parent's stream carries only
``subAgentActivity`` items (``started``, ``interacted``, ``completed``,
``interrupted``); the subagent's report lives on the child thread, so the
caller reads it there and attaches it to the item under
:data:`SUBAGENT_REPORT_KEY` before the item is rendered.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from waypoint.backends.task_notifications import (
    NOT_CAPTURED_ON_IMPORT,
    TaskNotification,
    task_notification_event,
)

SUBAGENT_ITEM_TYPE = "subAgentActivity"
SUBAGENT_TOOL_NAME = "Subagent"
# Kinds that end a subagent's run and so carry its report.
REPORT_KINDS = frozenset({"completed", "interrupted"})
REPORT_FETCH_TIMEOUT_SECONDS = 10.0
REPORT_UNAVAILABLE = "subagent report unavailable"
# Transient item key: set before rendering, stripped before persisting.
SUBAGENT_REPORT_KEY = "_waypointSubagentReport"

_TOOL_VERBS = {"started": "Spawned", "interacted": "Messaged"}
_CARD_STATES = {
    "completed": ("completed", "finished"),
    "interrupted": ("stopped", "stopped"),
}


@dataclass(frozen=True)
class SubagentReport:
    text: str | None
    # Why an over-long report's full text is not saved as an attachment;
    # ``None`` saves it.
    no_spill_reason: str | None


def _kind(item: dict[str, Any]) -> str:
    kind = item.get("kind")
    return kind if isinstance(kind, str) else ""


def _child_thread_id(item: dict[str, Any]) -> str | None:
    child = item.get("agentThreadId")
    return child if isinstance(child, str) and child else None


def display_name(agent_path: Any) -> str:
    """The last segment of an ``agentPath`` such as ``/root/reviewer``."""
    if isinstance(agent_path, str):
        segments = [part for part in agent_path.split("/") if part.strip()]
        if segments:
            return segments[-1].strip()
    return "subagent"


def report_child(item: dict[str, Any]) -> str | None:
    """The child thread to read a report from, for an item that ends a run."""
    if item.get("type") != SUBAGENT_ITEM_TYPE or _kind(item) not in REPORT_KINDS:
        return None
    return _child_thread_id(item)


def report_children(items: Iterable[dict[str, Any]]) -> list[str]:
    """Distinct child threads whose report the given items need, in order."""
    children: list[str] = []
    for item in items:
        child = report_child(item)
        if child is not None and child not in children:
            children.append(child)
    return children


def final_report(thread: Any) -> str | None:
    """The child's report: its last turn's final answer. A completed turn
    without one falls back to its last agent message; an unfinished or
    interrupted turn's other messages are progress, not a report."""
    turns = getattr(thread, "turns", None)
    if not turns:
        return None
    last_turn = turns[-1]
    messages = [
        item
        for item in (entry.root for entry in last_turn.items)
        if getattr(item, "type", None) == "agentMessage"
        and isinstance(getattr(item, "text", None), str)
        and item.text.strip()
    ]
    final = [
        message
        for message in messages
        if getattr(getattr(message, "phase", None), "value", None) == "final_answer"
    ]
    if not final and _value(getattr(last_turn, "status", None)) == "completed":
        final = messages
    if not final:
        return None
    text: str = final[-1].text
    return text.strip()


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def with_report(item: dict[str, Any], report: SubagentReport) -> dict[str, Any]:
    return {**item, SUBAGENT_REPORT_KEY: report}


def without_report(item: dict[str, Any]) -> dict[str, Any]:
    if SUBAGENT_REPORT_KEY not in item:
        return item
    return {key: value for key, value in item.items() if key != SUBAGENT_REPORT_KEY}


def tool_name(item: dict[str, Any]) -> str | None:
    return SUBAGENT_TOOL_NAME if _kind(item) in _TOOL_VERBS else None


def tool_text(item: dict[str, Any]) -> str | None:
    """``Spawned <name>`` / ``Messaged <name>``, or ``None`` for a kind that is
    not a tool entry."""
    verb = _TOOL_VERBS.get(_kind(item))
    if verb is None:
        return None
    return f"{verb} {display_name(item.get('agentPath'))}"


def task_card(item: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """The task-notification ``(text, metadata)`` for an item that ends a run."""
    state = _CARD_STATES.get(_kind(item))
    item_id = item.get("id")
    if state is None or not isinstance(item_id, str) or not item_id:
        return None
    status, verb = state
    attached = item.get(SUBAGENT_REPORT_KEY)
    report = (
        attached
        if isinstance(attached, SubagentReport)
        else SubagentReport(None, NOT_CAPTURED_ON_IMPORT)
    )
    agent_path = item.get("agentPath")
    notification = TaskNotification(
        id=item_id,
        kind="agent",
        status=status,
        summary=f'Agent "{display_name(agent_path)}" {verb}',
        task_id=_child_thread_id(item),
        result=report.text,
        output_unavailable_reason=None if report.text else REPORT_UNAVAILABLE,
    )
    text, metadata = task_notification_event(
        notification,
        allow_spill=report.no_spill_reason is None,
        no_spill_reason=report.no_spill_reason or "",
    )
    if isinstance(agent_path, str) and agent_path:
        metadata["agent_path"] = agent_path
    return text, metadata
