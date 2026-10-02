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

from openai_codex.client import CodexClient
from openai_codex.errors import (
    InvalidRequestError,
    JsonRpcError,
    MethodNotFoundError,
)
from openai_codex.generated.v2_all import (
    ThreadItemsListResponse,
    ThreadTurnsListResponse,
)

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

# A completion's item id is ``subagent-completed-<child turn id>``.
_COMPLETED_ID_PREFIX = "subagent-completed-"
# Items read from the end of a finished turn to find its report.
_REPORT_ITEMS_PAGE = 50

_TOOL_VERBS = {"started": "Spawned", "interacted": "Messaged"}
_CARD_STATES = {
    "completed": ("completed", "finished"),
    "interrupted": ("stopped", "stopped"),
}


@dataclass(frozen=True)
class ReportSource:
    thread_id: str
    # The child turn a completion reports on; ``None`` reads the newest turn.
    turn_id: str | None
    # Unix seconds. A newest turn that started later is a later task, not the
    # one the item reports on; an import sets it to the parent turn's end.
    started_by: int | None = None


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


def report_source(
    item: dict[str, Any], started_by: int | None = None
) -> ReportSource | None:
    """Where to read the report for an item that ends a run. ``started_by``
    bounds the newest turn read for an item that names no turn."""
    if item.get("type") != SUBAGENT_ITEM_TYPE or _kind(item) not in REPORT_KINDS:
        return None
    child = _child_thread_id(item)
    if child is None:
        return None
    item_id = item.get("id")
    turn_id = (
        item_id.removeprefix(_COMPLETED_ID_PREFIX)
        if isinstance(item_id, str) and item_id.startswith(_COMPLETED_ID_PREFIX)
        else None
    )
    if turn_id:
        return ReportSource(child, turn_id)
    return ReportSource(child, None, started_by)


def report_sources(
    items: Iterable[tuple[dict[str, Any], int | None]],
) -> list[ReportSource]:
    """Distinct report sources the given ``(item, started_by)`` pairs need, in
    order."""
    sources: list[ReportSource] = []
    for item, started_by in items:
        source = report_source(item, started_by)
        if source is not None and source not in sources:
            sources.append(source)
    return sources


def read_report(client: CodexClient, source: ReportSource) -> str | None:
    """Read a subagent's report from the turn it finished, or from its newest
    turn when the item does not name one. Blocking."""
    try:
        if source.turn_id is not None:
            page = client.request(
                "thread/items/list",
                {
                    "threadId": source.thread_id,
                    "turnId": source.turn_id,
                    "limit": _REPORT_ITEMS_PAGE,
                    "sortDirection": "desc",
                },
                response_model=ThreadItemsListResponse,
            )
            # A completion names a turn that has finished.
            return _report([entry.item for entry in reversed(page.data)], True)
        turns: list[Any] = client.request(
            "thread/turns/list",
            {"threadId": source.thread_id, "limit": 1, "itemsView": "full"},
            response_model=ThreadTurnsListResponse,
        ).data
    except (InvalidRequestError, MethodNotFoundError) as exc:
        if not _unsupported(exc):
            raise
        turns = client.thread_read(source.thread_id, True).thread.turns
        if source.turn_id is not None:
            turns = [turn for turn in turns if turn.id == source.turn_id]
    if turns and source.started_by is not None:
        started_at = getattr(turns[-1], "started_at", None)
        if isinstance(started_at, int) and started_at > source.started_by:
            return None
    return final_report(turns)


def _unsupported(exc: JsonRpcError) -> bool:
    """A CLI without the paginated thread reads: an unknown method, or a
    known one it does not serve yet."""
    return isinstance(exc, MethodNotFoundError) or "unknown variant" in exc.message


def final_report(turns: list[Any]) -> str | None:
    """The report in the last of ``turns``."""
    if not turns:
        return None
    last_turn = turns[-1]
    completed = _value(getattr(last_turn, "status", None)) == "completed"
    return _report(last_turn.items, completed)


def _report(entries: list[Any], turn_completed: bool) -> str | None:
    """A turn's final answer. A completed turn without one falls back to its
    last agent message; an unfinished or interrupted turn's other messages are
    progress, not a report."""
    messages = [
        item
        for item in (getattr(entry, "root", entry) for entry in entries)
        if getattr(item, "type", None) == "agentMessage"
        and isinstance(getattr(item, "text", None), str)
        and item.text.strip()
    ]
    final = [
        message
        for message in messages
        if _value(getattr(message, "phase", None)) == "final_answer"
    ]
    if not final and turn_completed:
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
