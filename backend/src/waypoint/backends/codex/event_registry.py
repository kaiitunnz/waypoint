"""Disposition of every Codex thread item type and notification method.

``ITEMS`` maps a thread item ``type`` to the renderers for its started,
updated, and completed notifications. ``NOTIFICATIONS`` maps a notification
method to a handler or an ``Ignored`` marker. ``test_codex_event_registry``
pins both tables to the SDK's item union and notification registry, so an SDK
bump fails CI until each new name has a disposition. Lookups that miss a table
take the fallback path, which only a CLI newer than the pinned SDK reaches.
"""

import base64
import binascii
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from waypoint.backends.codex import subagents
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
from waypoint.backends.events import mark_detail, mark_important
from waypoint.schemas import EventKind, SessionStatus

REASONING_ITEM_KIND = "reasoning"
# Methods whose deltas stream a reasoning item's text.
REASONING_DELTA_METHODS = frozenset(
    {
        "item/reasoning/summaryTextDelta",
        "item/reasoning/summaryPartAdded",
        "item/reasoning/textDelta",
    }
)
UNKNOWN_ITEM_MAX_BYTES = 4096


@dataclass(frozen=True)
class Rendered:
    kind: EventKind
    text: str
    status: SessionStatus = SessionStatus.RUNNING
    # Merged into the event's metadata after the adapter's envelope keys.
    metadata: dict[str, Any] = field(default_factory=dict)
    # Also log the text once per session: it is addressed to Waypoint's
    # developers more than to the human.
    log_once: bool = False

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
    # The item as stored in event metadata, for items carrying bulk the
    # transcript must not persist.
    persisted: Callable[[dict[str, Any]], dict[str, Any]] | None = None


@dataclass(frozen=True)
class Ignored:
    reason: str


NotificationSpec = NotificationHandler | Ignored


# ─── Item renderers ───


def _tool_call(text: str, metadata: dict[str, Any] | None = None) -> Rendered:
    return Rendered(EventKind.TOOL_CALL, text, metadata=metadata or {})


def _tool_result(text: str, metadata: dict[str, Any] | None = None) -> Rendered:
    return Rendered(EventKind.TOOL_RESULT, text, metadata=metadata or {})


def _note(text: str) -> Rendered:
    return Rendered(EventKind.SYSTEM_NOTE, text)


def _detail_note(text: str) -> Rendered:
    return Rendered(EventKind.SYSTEM_NOTE, text, metadata=mark_detail({}))


def _important_note(
    text: str, status: SessionStatus = SessionStatus.RUNNING
) -> Rendered:
    return Rendered(EventKind.SYSTEM_NOTE, text, status, mark_important({}))


def _joined_paths(changes: Any) -> str:
    if not isinstance(changes, list):
        return ""
    return ", ".join(
        str(change.get("path", "")) for change in changes if isinstance(change, dict)
    )


def _namespaced(item: dict[str, Any], key: str = "tool") -> str:
    name = item.get(key, "")
    namespace = item.get("namespace", "")
    return f"{namespace}:{name}" if namespace else str(name)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


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


def _mcp_completed(item: dict[str, Any]) -> Rendered:
    error = item.get("error")
    if isinstance(error, dict) and _text(error.get("message")):
        return _tool_result(_text(error.get("message")))
    result = item.get("result")
    content = result.get("content") if isinstance(result, dict) else None
    texts = _content_texts(content)
    if texts:
        return _tool_result("\n".join(texts))
    return _tool_result(f"MCP {item.get('server', '')}:{item.get('tool', '')}")


def _content_texts(content: Any) -> list[str]:
    """Text of an MCP / function-call content list; non-text parts become a
    bracketed type label."""
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            texts.append(text)
        elif isinstance(part.get("type"), str):
            texts.append(f"[{part['type']}]")
    return texts


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


def reasoning_text(item: dict[str, Any]) -> str:
    """A reasoning item's summary parts as paragraphs, falling back to its raw
    content."""
    for key in ("summary", "content"):
        parts = item.get(key)
        if isinstance(parts, list):
            text = "\n\n".join(part for part in parts if isinstance(part, str) and part)
            if text.strip():
                return text
    return ""


def _reasoning_output(text: str) -> Rendered:
    return Rendered(
        EventKind.AGENT_OUTPUT, text, metadata={"item_kind": REASONING_ITEM_KIND}
    )


def _reasoning_completed(item: dict[str, Any]) -> Rendered:
    text = reasoning_text(item)
    if text:
        return _reasoning_output(text)
    # Encrypted reasoning carries no text; the note only marks that it ran.
    return _detail_note("Reasoning")


def _sub_agent_activity(item: dict[str, Any]) -> Rendered | None:
    text = subagents.tool_text(item)
    if text is not None:
        # The activity has already happened, so it renders as a finished entry
        # with no pending call.
        return _tool_result(text, {"agent_path": item.get("agentPath")})
    card = subagents.task_card(item)
    if card is None:
        return None
    return Rendered(EventKind.SYSTEM_NOTE, card[0], metadata=card[1])


def _seconds(item: dict[str, Any]) -> str:
    duration = item.get("durationMs")
    if isinstance(duration, int | float) and not isinstance(duration, bool):
        return f"{duration / 1000:g}s"
    return "sleep"


_IMAGE_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF8", "image/gif", "gif"),
    (b"RIFF", "image/webp", "webp"),
)


def _decoded_image(result: Any) -> bytes | None:
    if not isinstance(result, str) or not result:
        return None
    try:
        return base64.b64decode(result, validate=True) or None
    except (binascii.Error, ValueError):
        return None


def _image_capture(item: dict[str, Any]) -> dict[str, Any]:
    """Capture keys that save a generated image as a pinned attachment.

    ``result`` is the base64 image, which also reaches a remote session's
    image; ``savedPath`` is only a host path, so it is the fallback.
    """
    saved_path = _text(item.get("savedPath"))
    result = item.get("result")
    data = _decoded_image(result)
    if data is not None:
        mime, extension = "image/png", "png"
        for signature, sig_mime, sig_extension in _IMAGE_SIGNATURES:
            if data.startswith(signature):
                mime, extension = sig_mime, sig_extension
                break
        filename = (
            os.path.basename(saved_path)
            if saved_path
            else f"{item.get('id') or 'image'}.{extension}"
        )
        return {
            "capture_inline_blobs": [
                {"filename": filename, "base64": result, "mime": mime}
            ]
        }
    if saved_path:
        return {"capture_host_files": [saved_path]}
    return {}


def _image_generation_completed(item: dict[str, Any]) -> Rendered:
    status = _text(item.get("status")) or "completed"
    failed = item.get("failure") is not None or status == "failed"
    metadata: dict[str, Any] = {"is_error": failed}
    if not failed:
        metadata.update(_image_capture(item))
    return _tool_result(status, metadata)


def _without_image_bytes(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "result": ""}


def _hook_prompt(item: dict[str, Any]) -> Rendered | None:
    fragments = item.get("fragments")
    if not isinstance(fragments, list):
        return None
    texts = [
        _text(fragment.get("text"))
        for fragment in fragments
        if isinstance(fragment, dict) and _text(fragment.get("text"))
    ]
    return _note("\n\n".join(texts)) if texts else None


def _function_call_output(item: dict[str, Any]) -> Rendered:
    output = item.get("output")
    if isinstance(output, str):
        text = output
    else:
        text = "\n".join(_content_texts(output))
    return _tool_result(text or _namespaced(item, "name") or "function call output")


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
        completed=_mcp_completed,
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
        started=lambda item: _note(item.get("text", "")),
        completed=lambda item: _note("Completed plan"),
    ),
    "agentMessage": ItemSpec(
        started=_agent_message_started,
        completed=_agent_message_completed,
    ),
    # Codex echoes the human's message, which the runtime already recorded.
    "userMessage": ItemSpec(),
    "reasoning": ItemSpec(completed=_reasoning_completed),
    "contextCompaction": ItemSpec(
        started=lambda item: _detail_note("Compacting context"),
        completed=lambda item: _important_note("Context compacted"),
    ),
    "subAgentActivity": ItemSpec(
        tool_name=subagents.tool_name,
        completed=_sub_agent_activity,
        persisted=subagents.without_report,
    ),
    "imageView": ItemSpec(
        tool_name=lambda item: "ViewImage",
        started=lambda item: _tool_call(_text(item.get("path")) or "view image"),
        completed=lambda item: _tool_result(_text(item.get("path")) or "view image"),
    ),
    "imageGeneration": ItemSpec(
        tool_name=lambda item: "ImageGeneration",
        started=lambda item: _tool_call(
            _text(item.get("revisedPrompt")) or "Generating image"
        ),
        completed=_image_generation_completed,
        persisted=_without_image_bytes,
    ),
    "enteredReviewMode": ItemSpec(
        started=lambda item: _important_note(
            f"Review started: {_text(item.get('review'))}".rstrip(": ")
        ),
    ),
    "exitedReviewMode": ItemSpec(
        completed=lambda item: Rendered(
            EventKind.AGENT_OUTPUT, _text(item.get("review"))
        ),
    ),
    "sleep": ItemSpec(
        tool_name=lambda item: "Sleep",
        started=lambda item: _tool_call(_seconds(item)),
        completed=lambda item: _tool_result(_seconds(item)),
    ),
    "hookPrompt": ItemSpec(completed=_hook_prompt),
    "functionCallOutput": ItemSpec(
        tool_name=lambda item: _namespaced(item, "name") or None,
        completed=_function_call_output,
    ),
    # Synthesized by the adapter from ``turn/plan/updated``.
    "todo_list": ItemSpec(
        started=lambda item: _tool_call(format_todo_list(item)),
        updated=lambda item: _tool_result(format_todo_list(item)),
        completed=lambda item: _tool_result(format_todo_list(item)),
    ),
}


def _unknown_item_type(item: dict[str, Any]) -> str:
    return _text(item.get("type")) or "item"


def _unknown_item_body(item: dict[str, Any]) -> str:
    body = json.dumps(
        {key: value for key, value in item.items() if key not in {"id", "type"}},
        separators=(",", ":"),
        default=str,
    )
    encoded = body.encode("utf-8")
    if len(encoded) <= UNKNOWN_ITEM_MAX_BYTES:
        return body
    return encoded[: UNKNOWN_ITEM_MAX_BYTES - 3].decode("utf-8", "ignore") + "…"


# An item type a newer CLI emits: rendered as a generic tool so it folds into
# tool runs and its payload stays inspectable.
_UNKNOWN_ITEM = ItemSpec(
    tool_name=_unknown_item_type,
    started=lambda item: _tool_call(_unknown_item_type(item)),
    completed=lambda item: _tool_result(_unknown_item_body(item)),
)


def item_spec(item_type: Any) -> ItemSpec:
    spec = ITEMS.get(item_type) if isinstance(item_type, str) else None
    return spec if spec is not None else _UNKNOWN_ITEM


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


def persisted_item(item: dict[str, Any]) -> dict[str, Any]:
    persisted = item_spec(item.get("type")).persisted
    return persisted(item) if persisted is not None else item


# ─── Notification handlers ───


def _delta(kind: EventKind) -> NotificationHandler:
    return lambda payload: Rendered(kind, str(payload.get("delta", "")))


def _reasoning_delta(payload: dict[str, Any]) -> Rendered:
    return _reasoning_output(str(payload.get("delta", "")))


def _reasoning_part_added(payload: dict[str, Any]) -> Rendered | None:
    index = payload.get("summaryIndex")
    if isinstance(index, int) and index > 0:
        return _reasoning_output("\n\n")
    return None


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
    return _note(f"Turn changes: +{additions} -{deletions}")


def _turn_completed(payload: dict[str, Any]) -> Rendered:
    turn = payload.get("turn", {})
    raw_status = turn.get("status", "completed")
    status = map_turn_status(raw_status)
    text = f"Turn {raw_status}"
    if raw_status == "completed":
        return Rendered(EventKind.SYSTEM_NOTE, text, status, mark_detail({}))
    return _important_note(text, status)


def _error(payload: dict[str, Any]) -> Rendered:
    return Rendered(
        EventKind.SYSTEM_NOTE,
        error_text(payload),
        SessionStatus.RUNNING if is_retryable_error(payload) else SessionStatus.ERROR,
    )


def _message_note(payload: dict[str, Any]) -> Rendered | None:
    message = _text(payload.get("message"))
    return _important_note(message) if message else None


def _summary_note(payload: dict[str, Any]) -> Rendered | None:
    parts = [_text(payload.get("summary")), _text(payload.get("details"))]
    text = "\n".join(part for part in parts if part)
    return _important_note(text) if text else None


def _deprecation_notice(payload: dict[str, Any]) -> Rendered | None:
    parts = [_text(payload.get("summary")), _text(payload.get("details"))]
    text = "\n".join(part for part in parts if part)
    if not text:
        return None
    return Rendered(
        EventKind.SYSTEM_NOTE, text, metadata=mark_detail({}), log_once=True
    )


def _model_rerouted(payload: dict[str, Any]) -> Rendered:
    reason = payload.get("reason")
    suffix = f" ({reason})" if isinstance(reason, str) and reason else ""
    return _important_note(
        f"Model rerouted: {payload.get('fromModel', '')} → "
        f"{payload.get('toModel', '')}{suffix}"
    )


def _auto_review_completed(payload: dict[str, Any]) -> Rendered:
    review = payload.get("review")
    review = review if isinstance(review, dict) else {}
    decision = _text(review.get("status")) or "completed"
    rationale = _text(review.get("rationale"))
    text = f"Auto-review {decision}" + (f": {rationale}" if rationale else "")
    target = payload.get("targetItemId")
    if isinstance(target, str) and target:
        # Merged into the reviewed item's card; the trailing newline keeps the
        # item's later output on its own line.
        return _tool_result(f"{text}\n", {"item_id": target})
    return _important_note(text)


def _hook_completed(payload: dict[str, Any]) -> Rendered | None:
    run = payload.get("run")
    if not isinstance(run, dict):
        return None
    status = _text(run.get("status"))
    if status == "completed":
        return None
    event_name = _text(run.get("eventName")) or "hook"
    message = _text(run.get("statusMessage"))
    text = f"Hook {event_name} {status or 'ended'}"
    return _important_note(f"{text}: {message}" if message else text)


_TURN_LESS_STATE = "Thread state Waypoint tracks through its own requests"
_NOT_A_SESSION_SURFACE = "Not part of a Waypoint session's transcript"

NOTIFICATIONS: dict[str, NotificationSpec] = {
    "item/started": lambda payload: render_item_started(extract_item(payload)),
    # Not in the pinned SDK registry; older CLIs send it for todo lists.
    "item/updated": lambda payload: render_item_updated(extract_item(payload)),
    "item/completed": lambda payload: render_item_completed(extract_item(payload)),
    "item/agentMessage/delta": _delta(EventKind.AGENT_OUTPUT),
    "item/commandExecution/outputDelta": _delta(EventKind.TOOL_RESULT),
    "item/fileChange/outputDelta": _delta(EventKind.TOOL_RESULT),
    "item/fileChange/patchUpdated": _patch_updated,
    "item/mcpToolCall/progress": lambda payload: (
        _tool_result(_text(payload.get("message")))
        if _text(payload.get("message"))
        else None
    ),
    "item/reasoning/summaryTextDelta": _reasoning_delta,
    "item/reasoning/summaryPartAdded": _reasoning_part_added,
    "item/reasoning/textDelta": _reasoning_delta,
    "item/autoApprovalReview/completed": _auto_review_completed,
    "item/autoApprovalReview/started": Ignored("The completed review carries it"),
    "autoApprovalReview/strictReviewRequired": lambda payload: _important_note(
        "Strict auto-review required"
    ),
    "item/plan/delta": Ignored("The completed plan item carries the full text"),
    "item/commandExecution/terminalInteraction": Ignored(
        "Stdin written to a running command; its output carries the effect"
    ),
    "turn/diff/updated": _turn_diff_updated,
    "turn/started": lambda payload: _detail_note("Turn started"),
    "turn/completed": _turn_completed,
    # Codex's update_plan tool, surfaced as a todo_list result so it renders in
    # the shared todo dock/card; the adapter synthesizes the todo_list item.
    "turn/plan/updated": lambda payload: _tool_result(
        format_plan(payload.get("plan", []))
    ),
    "turn/moderationMetadata": Ignored("Provider moderation metadata"),
    "error": _error,
    "warning": _message_note,
    # Narrates each auto-review; the decision already merges into the reviewed
    # item's card through item/autoApprovalReview/completed.
    "guardianWarning": lambda payload: (
        _detail_note(_text(payload.get("message")))
        if _text(payload.get("message"))
        else None
    ),
    "configWarning": _summary_note,
    # Codex deprecates calls Waypoint makes (e.g. on every thread resume).
    "deprecationNotice": _deprecation_notice,
    "model/rerouted": _model_rerouted,
    "model/verification": Ignored("Provider verification metadata"),
    "model/safetyBuffering/updated": Ignored("Provider buffering hint"),
    "hook/started": Ignored("Only a hook that does not succeed is shown"),
    "hook/completed": _hook_completed,
    "thread/tokenUsage/updated": Ignored("Handled by the adapter's usage path"),
    "thread/compacted": Ignored(
        "Deprecated upstream for the contextCompaction item; not sent to v2 clients"
    ),
    "thread/started": Ignored(_TURN_LESS_STATE),
    "thread/status/changed": Ignored(_TURN_LESS_STATE),
    "thread/name/updated": Ignored(_TURN_LESS_STATE),
    "thread/settings/updated": Ignored(_TURN_LESS_STATE),
    "thread/archived": Ignored(_TURN_LESS_STATE),
    "thread/unarchived": Ignored(_TURN_LESS_STATE),
    "thread/closed": Ignored(_TURN_LESS_STATE),
    "thread/deleted": Ignored(_TURN_LESS_STATE),
    "thread/reverted": Ignored(_TURN_LESS_STATE),
    "thread/goal/updated": Ignored(_TURN_LESS_STATE),
    "thread/goal/cleared": Ignored(_TURN_LESS_STATE),
    "thread/queue/changed": Ignored(_TURN_LESS_STATE),
    "thread/project/updated": Ignored(_TURN_LESS_STATE),
    "thread/attachment/updated": Ignored(_TURN_LESS_STATE),
    "thread/environment/connected": Ignored(_TURN_LESS_STATE),
    "thread/environment/disconnected": Ignored(_TURN_LESS_STATE),
    "serverRequest/resolved": Ignored("Waypoint resolves its own server requests"),
    "account/rateLimits/updated": Ignored("Owned by the Codex rate-limit source"),
    "account/updated": Ignored(_NOT_A_SESSION_SURFACE),
    "account/login/completed": Ignored("Login-scoped; routed to the login flow"),
    "account/gatewayOAuth/changed": Ignored(_NOT_A_SESSION_SURFACE),
    "modelProvider/authRecoveryStarted": Ignored(_NOT_A_SESSION_SURFACE),
    "modelProvider/authRecoveryCompleted": Ignored(_NOT_A_SESSION_SURFACE),
    "mcpServer/startupStatus/updated": Ignored(_NOT_A_SESSION_SURFACE),
    "mcpServer/oauthLogin/completed": Ignored(_NOT_A_SESSION_SURFACE),
    "mcpServer/event/stream/notification": Ignored(_NOT_A_SESSION_SURFACE),
    "skills/changed": Ignored("Skill lists are fetched on demand"),
    "app/list/updated": Ignored(_NOT_A_SESSION_SURFACE),
    "project/changed": Ignored(_NOT_A_SESSION_SURFACE),
    "remoteControl/status/changed": Ignored(_NOT_A_SESSION_SURFACE),
    "externalAgentConfig/import/progress": Ignored(_NOT_A_SESSION_SURFACE),
    "externalAgentConfig/import/completed": Ignored(_NOT_A_SESSION_SURFACE),
    "fs/changed": Ignored("Response to a request Waypoint does not make"),
    "fuzzyFileSearch/sessionUpdated": Ignored(
        "Response to a request Waypoint does not make"
    ),
    "fuzzyFileSearch/sessionCompleted": Ignored(
        "Response to a request Waypoint does not make"
    ),
    "command/exec/outputDelta": Ignored("Response to a request Waypoint does not make"),
    "process/outputDelta": Ignored("Response to a request Waypoint does not make"),
    "process/exited": Ignored("Response to a request Waypoint does not make"),
    "thread/realtime/started": Ignored("Realtime voice is not a Waypoint surface"),
    "thread/realtime/closed": Ignored("Realtime voice is not a Waypoint surface"),
    "thread/realtime/error": Ignored("Realtime voice is not a Waypoint surface"),
    "thread/realtime/sdp": Ignored("Realtime voice is not a Waypoint surface"),
    "thread/realtime/itemAdded": Ignored("Realtime voice is not a Waypoint surface"),
    "thread/realtime/item/started": Ignored("Realtime voice is not a Waypoint surface"),
    "thread/realtime/item/completed": Ignored(
        "Realtime voice is not a Waypoint surface"
    ),
    "thread/realtime/item/transcript/delta": Ignored(
        "Realtime voice is not a Waypoint surface"
    ),
    "thread/realtime/transcript/delta": Ignored(
        "Realtime voice is not a Waypoint surface"
    ),
    "thread/realtime/transcript/done": Ignored(
        "Realtime voice is not a Waypoint surface"
    ),
    "thread/realtime/outputAudio/delta": Ignored(
        "Realtime voice is not a Waypoint surface"
    ),
    "windows/worldWritableWarning": Ignored(
        "Windows sandbox setup; host is not Windows"
    ),
    "windowsSandbox/setupCompleted": Ignored(
        "Windows sandbox setup; host is not Windows"
    ),
}


def is_known_method(method: str) -> bool:
    return method in NOTIFICATIONS


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
