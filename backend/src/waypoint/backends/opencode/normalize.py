import json
from typing import Any

from waypoint.backends.diff_preview import (
    DiffPhase,
    build_preview,
    files_from_opencode_diffs,
    files_from_unified_diff,
    preview_to_metadata,
)
from waypoint.schemas import EventKind, SessionStatus


def map_event(
    event_type: str | None,
    properties: dict[str, Any],
) -> tuple[EventKind, str, dict[str, Any]]:
    if event_type is None:
        return (EventKind.SYSTEM_NOTE, "", {})

    # message.updated is intentionally not surfaced: runtime._record_user_event
    # already records user input when send_input is called, and assistant text
    # is streamed via message.part.delta below. Re-emitting here would duplicate
    # both user and assistant entries in the transcript.

    if event_type == "message.part.delta":
        delta = properties.get("delta", "")
        if not isinstance(delta, str) or not delta:
            return (EventKind.SYSTEM_NOTE, "", {})
        if properties.get("field") != "text":
            return (EventKind.SYSTEM_NOTE, "", {})
        # The adapter decorates delta payloads with the part type recorded
        # from the preceding message.part.updated *-start. Reasoning parts
        # share field="text" with regular text, so without this hint the
        # frontend can't tell scratchpad from final answer.
        part_type = properties.get("_waypoint_part_type")
        method = (
            "message.part.delta.reasoning"
            if part_type == "reasoning"
            else "message.part.delta.text"
        )
        metadata: dict[str, Any] = {
            "method": method,
            "payload": properties,
            "status": SessionStatus.RUNNING,
        }
        part_id = properties.get("partID")
        if isinstance(part_id, str) and part_id:
            metadata["item_id"] = part_id
        if part_type == "reasoning":
            metadata["item_kind"] = "reasoning"
        return (EventKind.AGENT_OUTPUT, delta, metadata)

    if event_type == "message.part.updated":
        part = properties.get("part", {})
        part_type = part.get("type")
        session_id = part.get("sessionID") or properties.get("sessionID", "")
        # text and reasoning parts are already streamed via message.part.delta;
        # the final part.updated snapshot would re-append the full body and
        # double the transcript entry. Tool calls and step boundaries are not
        # streamed, so they still surface here.
        if part_type == "text" or part_type == "reasoning":
            return (EventKind.SYSTEM_NOTE, "", {})
        elif part_type == "tool":
            return _map_tool_event(part, session_id)
        elif part_type == "step-start":
            return (
                EventKind.SYSTEM_NOTE,
                "Step started",
                {
                    "method": "message.part.updated.step-start",
                    "payload": properties,
                    "status": SessionStatus.RUNNING,
                },
            )
        elif part_type == "step-finish":
            reason = part.get("reason", "")
            cost = part.get("cost", 0)
            tokens = part.get("tokens", {})
            text = f"Step finished: {reason}"
            if cost:
                text += f" (${cost:.4f})"
            if tokens.get("output"):
                text += f" {tokens['output']} tokens"
            return (
                EventKind.SYSTEM_NOTE,
                text,
                {
                    "method": "message.part.updated.step-finish",
                    "payload": properties,
                    "status": SessionStatus.IDLE,
                },
            )

    if event_type == "session.status":
        status = properties.get("status", {})
        status_type = status.get("type")
        if status_type == "idle":
            return (
                EventKind.SYSTEM_NOTE,
                "Session idle",
                {
                    "method": "session.status.idle",
                    "payload": properties,
                    "status": SessionStatus.IDLE,
                },
            )
        elif status_type == "busy":
            return (
                EventKind.SYSTEM_NOTE,
                "Session busy",
                {
                    "method": "session.status.busy",
                    "payload": properties,
                    "status": SessionStatus.RUNNING,
                },
            )
        elif status_type == "retry":
            attempt = status.get("attempt", 0)
            message = status.get("message", "")
            return (
                EventKind.SYSTEM_NOTE,
                f"Retrying (attempt {attempt}): {message}",
                {
                    "method": "session.status.retry",
                    "payload": properties,
                    "status": SessionStatus.RUNNING,
                },
            )

    if event_type == "session.idle":
        return (
            EventKind.SYSTEM_NOTE,
            "Session ready",
            {
                "method": "session.idle",
                "payload": properties,
                "status": SessionStatus.IDLE,
            },
        )

    if event_type == "session.compacted":
        return (
            EventKind.SYSTEM_NOTE,
            "Session compacted",
            {
                "method": "session.compacted",
                "payload": properties,
                "status": SessionStatus.IDLE,
            },
        )

    if event_type == "session.error":
        error = properties.get("error")
        if error:
            error_name = error.get("name", "UnknownError")
            error_data = error.get("data", {})
            message = error_data.get("message", "Unknown error")
            return (
                EventKind.SYSTEM_NOTE,
                f"Error: {error_name} - {message}",
                {
                    "method": "session.error",
                    "payload": properties,
                    "status": SessionStatus.ERROR,
                },
            )
        return (
            EventKind.SYSTEM_NOTE,
            "Session error",
            {
                "method": "session.error",
                "payload": properties,
                "status": SessionStatus.ERROR,
            },
        )

    if event_type == "permission.asked":
        permission = properties
        tool_name = _permission_tool_name(permission)
        tool_input = _as_dict(permission.get("metadata")) or {}
        preview = _diff_preview_from_opencode_properties(properties, "proposed")
        text = f"{tool_name}: {permission.get('permission', 'Permission request')}"
        patterns = permission.get("patterns", [])
        if isinstance(patterns, list) and patterns:
            rendered = ", ".join(p for p in patterns if isinstance(p, str) and p)
            if rendered:
                text += f" ({rendered})"
        return (
            EventKind.APPROVAL_REQUEST,
            text,
            {
                "method": "permission.asked",
                "payload": properties,
                # Single canonical id for the frontend (events.ts reads
                # `approval.approval_id`). Older duplicate `permission_id`
                # and top-level `approval_id` keys were never consumed.
                "approval": {
                    "approval_id": permission.get("id"),
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "decisions": ["approve", "acceptForSession", "decline"],
                },
                "tool_name": tool_name,
                "tool_input": tool_input,
                "status": SessionStatus.WAITING_INPUT,
                **preview_to_metadata(preview),
            },
        )

    if event_type == "permission.replied":
        response = properties.get("reply", "")
        return (
            EventKind.SYSTEM_NOTE,
            f"Permission {response}",
            {
                "method": "permission.replied",
                "payload": properties,
                "status": SessionStatus.RUNNING,
            },
        )

    if event_type == "question.asked":
        request_id = properties.get("id")
        questions = _normalize_questions(properties.get("questions"))
        if not questions:
            return (EventKind.SYSTEM_NOTE, "", {})
        return (
            EventKind.TOOL_CALL,
            "Need your input",
            {
                "method": "question.asked",
                "payload": {"input": {"questions": questions}},
                "item_id": request_id,
                "item_type": "tool_use",
                "tool_name": "AskUserQuestion",
                "tool_input": {"questions": questions},
                "tool_use_id": request_id,
                "status": SessionStatus.WAITING_INPUT,
            },
        )

    if event_type == "question.replied":
        return (
            EventKind.SYSTEM_NOTE,
            "Question answered",
            {
                "method": "question.replied",
                "payload": properties,
                "status": SessionStatus.RUNNING,
            },
        )

    if event_type == "question.rejected":
        return (
            EventKind.SYSTEM_NOTE,
            "Question dismissed",
            {
                "method": "question.rejected",
                "payload": properties,
                "status": SessionStatus.RUNNING,
            },
        )

    if event_type == "command.executed":
        name = properties.get("name", "")
        arguments = properties.get("arguments", "")
        return (
            EventKind.SYSTEM_NOTE,
            f"Command: {name} {arguments}",
            {
                "method": "command.executed",
                "payload": properties,
                "status": SessionStatus.IDLE,
            },
        )

    if event_type == "file.edited":
        file_path = properties.get("file", "")
        preview = _diff_preview_from_opencode_properties(properties, "applied")
        return (
            EventKind.SYSTEM_NOTE,
            f"File edited: {file_path}",
            {
                "method": "file.edited",
                "payload": properties,
                "status": SessionStatus.RUNNING,
                **preview_to_metadata(preview),
            },
        )

    if event_type == "session.diff":
        diffs = properties.get("diff", [])
        if diffs:
            total_additions = sum(d.get("additions", 0) for d in diffs)
            total_deletions = sum(d.get("deletions", 0) for d in diffs)
            preview = build_preview("aggregate", files_from_opencode_diffs(diffs))
            return (
                EventKind.SYSTEM_NOTE,
                f"Changes: +{total_additions} -{total_deletions}",
                {
                    "method": "session.diff",
                    "payload": properties,
                    "status": SessionStatus.IDLE,
                    **preview_to_metadata(preview),
                },
            )

    if event_type == "todo.updated":
        todos = properties.get("todos", [])
        active = [t for t in todos if t.get("status") == "in_progress"]
        if active:
            return (
                EventKind.SYSTEM_NOTE,
                f"Active: {active[0].get('content', '')}",
                {
                    "method": "todo.updated",
                    "payload": properties,
                    "status": SessionStatus.RUNNING,
                },
            )

    return (EventKind.SYSTEM_NOTE, "", {})


def _map_tool_event(
    part: dict[str, Any], session_id: str
) -> tuple[EventKind, str, dict[str, Any]]:
    tool_name = part.get("tool", "tool")
    call_id = part.get("callID", "")
    state = part.get("state", {})
    state_status = state.get("status", "unknown")

    if state_status == "pending":
        tool_input = state.get("input", {})
        input_text = f"{tool_name}({json.dumps(tool_input, indent=2)})"
        return (
            EventKind.TOOL_CALL,
            input_text,
            {
                "method": "tool.pending",
                "item_id": call_id,
                "item_type": "tool_use",
                "call_id": call_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_use_id": call_id,
                "payload": part,
                "status": SessionStatus.RUNNING,
            },
        )
    elif state_status == "running":
        tool_input = state.get("input", {})
        title = state.get("title", f"Running {tool_name}")
        return (
            EventKind.TOOL_CALL,
            title,
            {
                "method": "tool.running",
                "item_id": call_id,
                "item_type": "tool_use",
                "call_id": call_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_use_id": call_id,
                "payload": part,
                "status": SessionStatus.RUNNING,
            },
        )
    elif state_status == "completed":
        output = state.get("output", "")
        tool_input = state.get("input", {})
        attachments = state.get("attachments", [])
        result_text = f"Result for {tool_name}:\n{output}"
        metadata = {
            "method": "tool.completed",
            "item_id": call_id,
            "item_type": "tool_result",
            "call_id": call_id,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": call_id,
            "payload": part,
            "status": SessionStatus.RUNNING,
        }
        if attachments:
            metadata["attachments"] = attachments
        return (
            EventKind.TOOL_RESULT,
            result_text,
            metadata,
        )
    elif state_status == "error":
        error = state.get("error", "Unknown error")
        return (
            EventKind.TOOL_RESULT,
            f"Error: {error}",
            {
                "method": "tool.error",
                "item_id": call_id,
                "item_type": "tool_result",
                "call_id": call_id,
                "tool_name": tool_name,
                "tool_use_id": call_id,
                "payload": part,
                "status": SessionStatus.RUNNING,
            },
        )

    return (EventKind.SYSTEM_NOTE, "", {})


def _diff_preview_from_opencode_properties(
    properties: dict[str, Any], phase: DiffPhase
):
    files = files_from_opencode_diffs(properties.get("diff"))
    if files:
        return build_preview(phase, files)

    metadata = _as_dict(properties.get("metadata")) or {}
    for key in ("diff", "patch"):
        value = metadata.get(key) or properties.get(key)
        if isinstance(value, str) and value:
            fallback = str(properties.get("file") or "changes")
            return build_preview(phase, files_from_unified_diff(value, fallback))
    for key in ("changes", "fileChanges", "files"):
        value = metadata.get(key) or properties.get(key)
        files = files_from_opencode_diffs(value)
        if files:
            return build_preview(phase, files)
    return None


def format_event_text(event_type: str, properties: dict[str, Any]) -> str:
    _, text, _ = map_event(event_type, properties)
    return text


def _permission_tool_name(permission: dict[str, Any]) -> str:
    metadata = _as_dict(permission.get("metadata")) or {}
    tool = metadata.get("tool")
    if isinstance(tool, str) and tool:
        return tool
    permission_name = permission.get("permission")
    if isinstance(permission_name, str) and permission_name:
        return permission_name.replace("_", " ").title()
    return "Permission"


def _normalize_questions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        question = entry.get("question")
        options_raw = entry.get("options")
        if not isinstance(question, str) or not isinstance(options_raw, list):
            continue
        options: list[dict[str, str]] = []
        for option in options_raw:
            if not isinstance(option, dict):
                continue
            label = option.get("label")
            if not isinstance(label, str) or not label:
                continue
            option_payload: dict[str, str] = {"label": label}
            description = option.get("description")
            if isinstance(description, str) and description:
                option_payload["description"] = description
            options.append(option_payload)
        if not options:
            continue
        payload: dict[str, Any] = {
            "question": question,
            "options": options,
        }
        header = entry.get("header")
        if isinstance(header, str) and header:
            payload["header"] = header
        if entry.get("multiple") is True:
            payload["multiSelect"] = True
        normalized.append(payload)
    return normalized


def _as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None
