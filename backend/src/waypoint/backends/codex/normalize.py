"""Pure helpers over Codex notification payloads and thread items.

The per-type mapping to ``(kind, text, status)`` lives in
``event_registry``; these helpers extract ids, outcomes, diff previews, and
plan/todo shapes that both the live adapter and history import share.
"""

from dataclasses import asdict, is_dataclass
from typing import Any

from openai_codex.models import UnknownNotification

from waypoint.backends.diff_preview import (
    DiffPreviewPayload,
    build_preview,
    files_from_codex_file_changes,
    files_from_codex_legacy_file_changes,
    files_from_unified_diff,
)
from waypoint.schemas import SessionStatus


def is_retryable_error(payload: dict[str, Any]) -> bool:
    """Codex is retrying the model stream behind this ``error``; the turn
    is still live."""
    return payload.get("willRetry") is True


def error_text(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if not isinstance(error, dict):
        return "Codex error"
    message = str(error.get("message") or "Codex error")
    details = error.get("additionalDetails")
    if not isinstance(details, str):
        return message
    details = details.strip()
    if not details or details == message:
        return message
    return f"{message} — {details}"


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


# Codex item types whose completed form carries a terminal status (commands
# also carry an exitCode).
_OUTCOME_ITEM_TYPES = frozenset(
    {
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "dynamicToolCall",
        "collabAgentToolCall",
    }
)


def tool_result_is_error(item_type: str | None, item: dict[str, Any]) -> bool | None:
    """Map a completed Codex tool item's status/exitCode to the neutral
    ``is_error`` flag, returning ``None`` when the outcome is indeterminate."""
    if item_type not in _OUTCOME_ITEM_TYPES:
        return None
    status = item.get("status")
    if status == "completed":
        return False
    if status in {"failed", "declined"}:
        return True
    if item_type == "commandExecution":
        exit_code = item.get("exitCode")
        if isinstance(exit_code, int):
            return exit_code != 0
    return None


def set_completed_outcome(
    metadata: dict[str, Any], item_type: str | None, item: dict[str, Any]
) -> None:
    """Stamp ``metadata["is_error"]`` for a completed tool item when its
    outcome is determinable."""
    is_error = tool_result_is_error(item_type, item)
    if is_error is not None:
        metadata["is_error"] = is_error


_PLAN_DECISIONS: tuple[str, ...] = ("accept", "acceptForSession", "decline", "cancel")


def plan_metadata_for_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Return a normalised plan envelope for a Codex ``plan`` item.

    Frontends consume this directly so they don't have to re-derive the
    plan id / text from the raw codex item shape, and so the same view
    model can express Claude's ExitPlanMode flow in a future change.
    """

    if item.get("type") != "plan":
        return None
    plan_id = item.get("id")
    text = item.get("text", "")
    if not isinstance(text, str):
        text = ""
    return {
        "id": plan_id if isinstance(plan_id, str) and plan_id else None,
        "text": text,
        "source": "codex",
        "decisions": list(_PLAN_DECISIONS),
    }


def diff_preview_for_notification(
    method: str, payload: dict[str, Any]
) -> DiffPreviewPayload | None:
    if method in {"item/started", "item/updated", "item/completed"}:
        item = extract_item(payload)
        if item.get("type") != "fileChange":
            return None
        if method == "item/completed":
            return build_preview(
                "applied", files_from_codex_file_changes(item.get("changes"))
            )
        return build_preview(
            "proposed", files_from_codex_file_changes(item.get("changes"))
        )
    if method == "item/fileChange/patchUpdated":
        return build_preview(
            "proposed", files_from_codex_file_changes(payload.get("changes"))
        )
    if method == "turn/diff/updated":
        diff = payload.get("diff")
        if not isinstance(diff, str):
            return None
        return build_preview("aggregate", files_from_unified_diff(diff, "Turn changes"))
    return None


def diff_preview_for_approval(
    method: str,
    params: dict[str, Any],
    cached: DiffPreviewPayload | None = None,
) -> DiffPreviewPayload | None:
    if method == "item/fileChange/requestApproval":
        return cached
    if method == "applyPatchApproval":
        return build_preview(
            "proposed", files_from_codex_legacy_file_changes(params.get("fileChanges"))
        )
    return None


# Codex update_plan step statuses → the canonical todo statuses the frontend
# reads (`in_progress`, not Codex's `inProgress`).
_PLAN_TODO_STATUS = {
    "completed": "completed",
    "inProgress": "in_progress",
    "pending": "pending",
}


def plan_todo_items(plan: Any) -> list[dict[str, Any]]:
    """Map a Codex ``turn/plan/updated`` plan into todo_list item entries."""
    if not isinstance(plan, list):
        return []
    items: list[dict[str, Any]] = []
    for entry in plan:
        if not isinstance(entry, dict):
            continue
        items.append(
            {
                "text": str(entry.get("step", "")),
                "status": _PLAN_TODO_STATUS.get(
                    str(entry.get("status", "")), "pending"
                ),
            }
        )
    return items


def format_plan(plan: Any) -> str:
    if not isinstance(plan, list):
        return ""
    lines = [
        f"- {entry.get('step', '')} [{entry.get('status', '')}]"
        for entry in plan
        if isinstance(entry, dict)
    ]
    return "\n".join(lines)


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
