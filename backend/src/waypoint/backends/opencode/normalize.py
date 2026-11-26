"""OpenCode 2.x events and history messages as Waypoint events.

Every function returns ``(kind, text, metadata)``; the live path adds the
session status.
"""

import json
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from waypoint.backends.diff_preview import (
    DiffPhase,
    build_preview,
    files_from_opencode_diffs,
    preview_to_metadata,
)
from waypoint.backends.events import (
    INTERACTION_METADATA_KEY,
    InteractionChoice,
    InteractionEnvelope,
    question_interaction,
)
from waypoint.backends.opencode.forms import Form
from waypoint.backends.opencode.sessions import Permission
from waypoint.questions import ASK_QUESTION_CLOSED, QuestionCloseReason
from waypoint.schemas import EventKind, SessionStatus

type Emission = tuple[EventKind, str, dict[str, Any]]

APPROVAL_DECISIONS = ("approve", "acceptForSession", "decline")

# The tool that asks questions; the form it raises is the question card.
QUESTION_TOOL = "question"

# OpenCode's tool names where Waypoint's clients and telemetry know another.
_TOOL_NAMES = {"shell": "bash", "subagent": "task"}
_FILE_TOOLS = frozenset({"read", "write", "edit"})


def tool_name(name: str) -> str:
    return _TOOL_NAMES.get(name, name)


def tool_input(name: str, raw: Any) -> dict[str, Any]:
    values = dict(raw) if isinstance(raw, dict) else {}
    if name in _FILE_TOOLS and isinstance(path := values.get("path"), str):
        values["file_path"] = path
    return values


def note(text: str, method: str, **metadata: Any) -> Emission:
    return (EventKind.SYSTEM_NOTE, text, {"method": method, **metadata})


def part_id(message_id: str, ordinal: int, *, reasoning: bool) -> str:
    """The Waypoint item id of one text or reasoning part."""
    return f"{message_id}:{'reasoning' if reasoning else 'text'}:{ordinal}"


def text_delta(
    message_id: str, ordinal: int, delta: str, *, reasoning: bool
) -> Emission:
    part = "reasoning" if reasoning else "text"
    metadata: dict[str, Any] = {
        "method": f"session.{part}.delta",
        "item_id": part_id(message_id, ordinal, reasoning=reasoning),
    }
    if reasoning:
        metadata["item_kind"] = "reasoning"
    return (EventKind.AGENT_OUTPUT, delta, metadata)


def tool_call(call_id: str, name: str, raw_input: Any) -> Emission:
    shown, values = tool_name(name), tool_input(name, raw_input)
    return (
        EventKind.TOOL_CALL,
        f"{shown}({json.dumps(values, indent=2)})",
        {
            "method": "session.tool.called",
            "item_id": call_id,
            "item_type": "tool_use",
            "call_id": call_id,
            "tool_use_id": call_id,
            "tool_name": shown,
            "tool_input": values,
        },
    )


def tool_result(
    call_id: str,
    name: str,
    raw_input: Any,
    *,
    content: Any = None,
    error: Any = None,
    files: Any = None,
) -> Emission:
    """A finished call, failed when ``error`` is set. ``files`` are the diffs it
    applied."""
    shown = tool_name(name)
    failed = error is not None
    if failed:
        message = error.get("message") if isinstance(error, dict) else error
        text = f"Error: {message or 'Unknown error'}"
    else:
        text = f"Result for {shown}:\n{_content_text(content)}"
    return (
        EventKind.TOOL_RESULT,
        text,
        {
            "method": "session.tool.failed" if failed else "session.tool.success",
            "item_id": call_id,
            "item_type": "tool_result",
            "call_id": call_id,
            "tool_use_id": call_id,
            "tool_name": shown,
            "tool_input": tool_input(name, raw_input),
            "is_error": failed,
            **({} if failed else _diff_metadata("applied", files)),
        },
    )


def approval_request(
    permission: Permission,
    *,
    name: str | None,
    raw_input: Any,
    from_subagent: bool,
) -> Emission:
    """A permission request, with the tool call it came from when known."""
    request_id, action = permission.id, permission.action
    resources = list(permission.resources)

    shown = tool_name(name) if name else action.replace("_", " ").title()
    values = tool_input(name or "", raw_input) or {
        "action": action,
        "resources": resources,
    }
    title = f"{shown}: {action}"
    if resources:
        title += f" ({', '.join(resources)})"
    if from_subagent:
        title = f"Subagent · {title}"

    interaction = InteractionEnvelope(
        kind="approval",
        request_id=request_id,
        title=title,
        choices=[InteractionChoice(label=decision) for decision in APPROVAL_DECISIONS],
    )
    metadata: dict[str, Any] = {
        "method": "permission.asked",
        "approval_id": request_id,
        "approval": {
            "approval_id": request_id,
            "tool_name": shown,
            "tool_input": values,
            "decisions": list(APPROVAL_DECISIONS),
        },
        "tool_name": shown,
        "tool_input": values,
        INTERACTION_METADATA_KEY: interaction.to_metadata(),
        **_diff_metadata("proposed", permission.files),
    }
    if permission.call_id:
        metadata["item_id"] = permission.call_id
    return (EventKind.APPROVAL_REQUEST, title, metadata)


def approval_invalidated(request_id: str) -> Emission:
    return note(
        "Approval request is no longer pending",
        "approval.invalidated",
        approval_id=request_id,
    )


def question(form: Form) -> Emission | None:
    questions = form.questions()
    if not questions:
        return None
    metadata: dict[str, Any] = {
        "method": "form.created",
        "payload": {"input": {"questions": questions}},
        "item_id": form.id,
        "item_type": "tool_use",
        "tool_use_id": form.id,
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": questions},
    }
    if (envelope := question_interaction(form.id, questions)) is not None:
        metadata[INTERACTION_METADATA_KEY] = envelope.to_metadata()
    return (EventKind.TOOL_CALL, "Need your input", metadata)


def question_closed(form_id: str, *, answered: bool) -> Emission:
    return note(
        "Question answered" if answered else "Question dismissed",
        "form.replied" if answered else "form.cancelled",
        kind=ASK_QUESTION_CLOSED,
        tool_use_id=form_id,
        reason=(
            QuestionCloseReason.PROVIDER_REPLIED
            if answered
            else QuestionCloseReason.PROVIDER_REJECTED
        ).value,
    )


def turn_changes(diffs: Any) -> Emission | None:
    """The files a turn changed, from ``GET /api/session/{id}/diff``."""
    preview = build_preview("aggregate", files_from_opencode_diffs(diffs))
    if preview is None:
        return None
    return note(
        f"Changes: +{preview.total_additions} -{preview.total_deletions}",
        "session.diff",
        **preview_to_metadata(preview),
    )


def historical_events_from_messages(
    messages: Sequence[Any],
) -> list[tuple[datetime, EventKind, str, dict[str, Any]]]:
    """``GET /api/session/{id}/message``, oldest first, as transcript events."""
    events: list[tuple[datetime, EventKind, str, dict[str, Any]]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        created = _timestamp(message, "created") or datetime.now(UTC)
        match message.get("type"):
            case "user":
                if text := message.get("text"):
                    events.append(
                        (created, EventKind.USER_INPUT, str(text), {"submit": True})
                    )
            case "assistant":
                events.extend(_historical_assistant(message, created))
            case "shell":
                events.extend(_historical_shell(message, created))
    return [
        (ts, kind, text, {**metadata, "status": SessionStatus.RUNNING})
        for ts, kind, text, metadata in events
    ]


def _historical_shell(
    message: dict[str, Any], created: datetime
) -> list[tuple[datetime, EventKind, str, dict[str, Any]]]:
    """A command the human ran from OpenCode's own composer."""
    call_id = str(message.get("shellID") or message.get("id"))
    raw_input = {"command": message.get("command")}
    output = message.get("output")
    text = output.get("output") if isinstance(output, dict) else ""
    content = [{"type": "text", "text": text}]
    finished = _timestamp(message, "completed") or created
    return [
        (created, *tool_call(call_id, "shell", raw_input)),
        (finished, *tool_result(call_id, "shell", raw_input, content=content)),
    ]


def _historical_assistant(
    message: dict[str, Any], created: datetime
) -> list[tuple[datetime, EventKind, str, dict[str, Any]]]:
    message_id = str(message.get("id"))
    finished = _timestamp(message, "completed") or created
    ordinals: Counter[str] = Counter()
    events: list[tuple[datetime, EventKind, str, dict[str, Any]]] = []
    for part in message.get("content") or ():
        if not isinstance(part, dict):
            continue
        match part.get("type"):
            case "text" | "reasoning" as kind:
                ordinal = ordinals[kind]
                ordinals[kind] += 1
                if text := part.get("text"):
                    delta = text_delta(
                        message_id, ordinal, str(text), reasoning=kind == "reasoning"
                    )
                    events.append((finished, *delta))
            case "tool":
                events.extend(_historical_tool(part, finished))
    return events


def _historical_tool(
    part: dict[str, Any], fallback: datetime
) -> list[tuple[datetime, EventKind, str, dict[str, Any]]]:
    call_id, name = str(part.get("id")), str(part.get("name") or "tool")
    state = part.get("state")
    state = state if isinstance(state, dict) else {}
    started = _timestamp(part, "created") or fallback
    events = [(started, *tool_call(call_id, name, state.get("input")))]
    status = state.get("status")
    if status in ("completed", "error"):
        details = state.get("metadata")
        result = tool_result(
            call_id,
            name,
            state.get("input"),
            content=state.get("content"),
            error=state.get("error") if status == "error" else None,
            files=details.get("files") if isinstance(details, dict) else None,
        )
        events.append((_timestamp(part, "completed") or fallback, *result))
    return events


def _content_text(content: Any) -> str:
    """The text of a tool's content blocks, each starting on its own line."""
    text = ""
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        if text and not text.endswith("\n"):
            text += "\n"
        text += str(block.get("text") or "")
    return text


def _diff_metadata(phase: DiffPhase, files: Any) -> dict[str, Any]:
    return preview_to_metadata(build_preview(phase, files_from_opencode_diffs(files)))


def _timestamp(item: dict[str, Any], key: str) -> datetime | None:
    times = item.get("time")
    value = times.get(key) if isinstance(times, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)
