import base64
from typing import Any

import pytest
from openai_codex.generated.notification_registry import NOTIFICATION_MODELS

from waypoint.backends.codex._sdk_compat import KNOWN_THREAD_ITEM_TYPES
from waypoint.backends.codex.event_registry import (
    ITEMS,
    NOTIFICATIONS,
    UNKNOWN_ITEM_MAX_BYTES,
    Ignored,
    Rendered,
    extract_tool_name,
    persisted_item,
    render_item_completed,
    render_item_started,
    render_notification,
)
from waypoint.backends.events import (
    DETAIL_VISIBILITY,
    IMPORTANT_VISIBILITY,
    VISIBILITY_METADATA_KEY,
)
from waypoint.schemas import EventKind, SessionStatus

# The adapter synthesizes todo_list items from turn/plan/updated.
SYNTHETIC_ITEM_TYPES = {"todo_list"}
# Sent by older CLIs; absent from the pinned SDK registry.
EXTRA_METHODS = {"item/updated"}
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _visibility(rendered: Rendered | None) -> Any:
    assert rendered is not None
    return rendered.metadata.get(VISIBILITY_METADATA_KEY)


def test_item_table_covers_the_pinned_thread_item_union() -> None:
    expected = set(KNOWN_THREAD_ITEM_TYPES) | SYNTHETIC_ITEM_TYPES
    missing = sorted(expected - set(ITEMS))
    extra = sorted(set(ITEMS) - expected)
    assert not missing, f"thread item types without a disposition: {missing}"
    assert not extra, f"item entries the SDK no longer models: {extra}"


def test_notification_table_covers_the_pinned_registry() -> None:
    expected = set(NOTIFICATION_MODELS) | EXTRA_METHODS
    missing = sorted(expected - set(NOTIFICATIONS))
    extra = sorted(set(NOTIFICATIONS) - expected)
    assert not missing, f"notification methods without a disposition: {missing}"
    assert not extra, f"notification entries the SDK no longer sends: {extra}"


def test_every_ignored_entry_states_a_reason() -> None:
    for method, spec in NOTIFICATIONS.items():
        if isinstance(spec, Ignored):
            assert spec.reason.strip(), method


def test_turn_lifecycle_notes_are_detail_except_failures() -> None:
    started = render_notification("turn/started", {"turn": {"id": "t1"}})
    assert started is not None and started.text == "Turn started"
    assert _visibility(started) == DETAIL_VISIBILITY
    completed = render_notification(
        "turn/completed", {"turn": {"id": "t1", "status": "completed"}}
    )
    assert completed is not None and completed.status is SessionStatus.IDLE
    assert _visibility(completed) == DETAIL_VISIBILITY
    for raw, status in (
        ("interrupted", SessionStatus.INTERRUPTED),
        ("failed", SessionStatus.ERROR),
    ):
        ended = render_notification(
            "turn/completed", {"turn": {"id": "t1", "status": raw}}
        )
        assert ended is not None and ended.status is status
        assert _visibility(ended) == IMPORTANT_VISIBILITY


def test_user_message_echo_renders_nothing() -> None:
    item = {"type": "userMessage", "id": "u1", "content": []}
    assert render_item_started(item) is None
    assert render_item_completed(item) is None


@pytest.mark.parametrize(
    ("item", "text"),
    [
        ({"summary": ["First.", "Second."], "content": ["raw"]}, "First.\n\nSecond."),
        ({"summary": [], "content": ["raw one", "raw two"]}, "raw one\n\nraw two"),
    ],
)
def test_reasoning_completion_renders_summary_then_content(
    item: dict[str, Any], text: str
) -> None:
    rendered = render_item_completed({"type": "reasoning", "id": "rs1", **item})
    assert rendered is not None
    assert rendered.kind is EventKind.AGENT_OUTPUT
    assert rendered.text == text
    assert rendered.metadata["item_kind"] == "reasoning"
    assert render_item_started({"type": "reasoning", "id": "rs1", **item}) is None


def test_empty_reasoning_is_one_detail_note() -> None:
    item = {"type": "reasoning", "id": "rs1", "summary": [], "content": []}
    rendered = render_item_completed(item)
    assert rendered is not None
    assert (rendered.kind, rendered.text) == (EventKind.SYSTEM_NOTE, "Reasoning")
    assert _visibility(rendered) == DETAIL_VISIBILITY


def test_reasoning_deltas_stream_into_the_thinking_disclosure() -> None:
    delta = render_notification(
        "item/reasoning/summaryTextDelta",
        {"itemId": "rs1", "delta": "Looking", "summaryIndex": 0},
    )
    assert delta is not None
    assert (delta.kind, delta.text) == (EventKind.AGENT_OUTPUT, "Looking")
    assert delta.metadata == {"item_kind": "reasoning"}
    raw = render_notification(
        "item/reasoning/textDelta", {"itemId": "rs1", "delta": "x"}
    )
    assert raw is not None and raw.metadata == {"item_kind": "reasoning"}
    first = render_notification(
        "item/reasoning/summaryPartAdded", {"itemId": "rs1", "summaryIndex": 0}
    )
    assert first is None
    later = render_notification(
        "item/reasoning/summaryPartAdded", {"itemId": "rs1", "summaryIndex": 1}
    )
    assert later is not None and later.text == "\n\n"


def test_context_compaction_marks_start_detail_and_completion_important() -> None:
    item = {"type": "contextCompaction", "id": "c1"}
    started = render_item_started(item)
    completed = render_item_completed(item)
    assert started is not None and started.text == "Compacting context"
    assert _visibility(started) == DETAIL_VISIBILITY
    assert completed is not None and completed.text == "Context compacted"
    assert _visibility(completed) == IMPORTANT_VISIBILITY


def test_sub_agent_activity_is_a_detail_note() -> None:
    item = {
        "type": "subAgentActivity",
        "id": "a1",
        "kind": "started",
        "agentPath": "/root/worker",
        "agentThreadId": "th2",
    }
    assert render_item_started(item) is None
    rendered = render_item_completed(item)
    assert rendered is not None
    assert rendered.text == "Subagent started: /root/worker"
    assert _visibility(rendered) == DETAIL_VISIBILITY


def test_image_view_is_a_view_image_tool_pair() -> None:
    item = {"type": "imageView", "id": "v1", "path": "/tmp/shot.png"}
    call = render_item_started(item)
    result = render_item_completed(item)
    assert call is not None and call.kind is EventKind.TOOL_CALL
    assert result is not None and result.kind is EventKind.TOOL_RESULT
    assert call.text == result.text == "/tmp/shot.png"
    assert extract_tool_name("imageView", item) == "ViewImage"


def _image_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "imageGeneration",
        "id": "ig1",
        "status": "completed",
        "revisedPrompt": "A red circle",
        "result": base64.b64encode(PNG_BYTES).decode(),
        "savedPath": "/home/u/.codex/generated_images/t/ig1.png",
        "failure": None,
    }
    item.update(overrides)
    return item


def test_image_generation_captures_the_base64_image() -> None:
    item = _image_item()
    call = render_item_started({**item, "revisedPrompt": None, "result": ""})
    assert call is not None and call.text == "Generating image"
    result = render_item_completed(item)
    assert result is not None
    assert (result.kind, result.text) == (EventKind.TOOL_RESULT, "completed")
    assert result.metadata["is_error"] is False
    assert result.metadata["capture_inline_blobs"] == [
        {"filename": "ig1.png", "base64": item["result"], "mime": "image/png"}
    ]
    assert extract_tool_name("imageGeneration", item) == "ImageGeneration"


def test_image_generation_is_persisted_without_its_bytes() -> None:
    item = _image_item()
    stored = persisted_item(item)
    assert stored["result"] == ""
    assert stored["savedPath"] == item["savedPath"]
    assert item["result"], "the live item keeps its bytes for capture"


def test_image_generation_falls_back_to_the_saved_path() -> None:
    result = render_item_completed(_image_item(result="not base64!"))
    assert result is not None
    assert "capture_inline_blobs" not in result.metadata
    assert result.metadata["capture_host_files"] == [
        "/home/u/.codex/generated_images/t/ig1.png"
    ]


def test_failed_image_generation_is_an_error_without_capture() -> None:
    result = render_item_completed(
        _image_item(status="failed", result="", failure={"type": "usageLimitExceeded"})
    )
    assert result is not None
    assert result.metadata == {"is_error": True}


def test_review_mode_renders_the_start_note_and_the_review() -> None:
    entered = render_item_started(
        {"type": "enteredReviewMode", "id": "r1", "review": "current changes"}
    )
    assert entered is not None
    assert entered.text == "Review started: current changes"
    assert _visibility(entered) == IMPORTANT_VISIBILITY
    exited = render_item_completed(
        {"type": "exitedReviewMode", "id": "r2", "review": "No issues found."}
    )
    assert exited is not None
    assert (exited.kind, exited.text) == (EventKind.AGENT_OUTPUT, "No issues found.")


def test_sleep_hook_prompt_and_function_call_output() -> None:
    sleep = {"type": "sleep", "id": "s1", "durationMs": 1500}
    call = render_item_started(sleep)
    assert call is not None and call.text == "1.5s"
    assert extract_tool_name("sleep", sleep) == "Sleep"

    hook = render_item_completed(
        {
            "type": "hookPrompt",
            "id": "h1",
            "fragments": [
                {"hookRunId": "r", "text": "Run tests first."},
                {"hookRunId": "r", "text": "Then lint."},
            ],
        }
    )
    assert hook is not None and hook.kind is EventKind.SYSTEM_NOTE
    assert hook.text == "Run tests first.\n\nThen lint."

    output = {
        "type": "functionCallOutput",
        "id": "f1",
        "name": "lookup",
        "namespace": "tools",
        "output": [{"type": "inputText", "text": "found"}, {"type": "inputImage"}],
    }
    assert render_item_started(output) is None
    result = render_item_completed(output)
    assert result is not None and result.kind is EventKind.TOOL_RESULT
    assert result.text == "found\n[inputImage]"
    assert extract_tool_name("functionCallOutput", output) == "tools:lookup"


def test_mcp_tool_call_completion_carries_its_result() -> None:
    item = {
        "type": "mcpToolCall",
        "id": "m1",
        "server": "docs",
        "tool": "search",
        "status": "completed",
        "result": {"content": [{"type": "text", "text": "3 hits"}]},
    }
    result = render_item_completed(item)
    assert result is not None and result.text == "3 hits"
    failed = render_item_completed(
        {**item, "status": "failed", "result": None, "error": {"message": "boom"}}
    )
    assert failed is not None and failed.text == "boom"


@pytest.mark.parametrize(
    ("method", "payload", "text"),
    [
        ("warning", {"message": "Disk almost full"}, "Disk almost full"),
        (
            "configWarning",
            {"summary": "Unknown key", "details": "line 3"},
            "Unknown key\nline 3",
        ),
        ("deprecationNotice", {"summary": "Old flag"}, "Old flag"),
        (
            "model/rerouted",
            {
                "fromModel": "a",
                "toModel": "b",
                "reason": "highRiskCyberActivity",
            },
            "Model rerouted: a → b (highRiskCyberActivity)",
        ),
        (
            "autoApprovalReview/strictReviewRequired",
            {"threadId": "t", "turnId": "u", "startedAtMs": 1},
            "Strict auto-review required",
        ),
    ],
)
def test_known_notifications_render_important_notes(
    method: str, payload: dict[str, Any], text: str
) -> None:
    rendered = render_notification(method, payload)
    assert rendered is not None
    assert (rendered.kind, rendered.text) == (EventKind.SYSTEM_NOTE, text)
    assert _visibility(rendered) == IMPORTANT_VISIBILITY


def test_guardian_warning_is_a_detail_note() -> None:
    rendered = render_notification(
        "guardianWarning",
        {"message": "Automatic approval review approved", "threadId": "t"},
    )
    assert rendered is not None
    assert rendered.text == "Automatic approval review approved"
    assert _visibility(rendered) == DETAIL_VISIBILITY


def test_auto_review_completion_merges_into_the_reviewed_item() -> None:
    payload = {
        "targetItemId": "exec-1",
        "review": {"status": "denied", "rationale": "Deletes the repo"},
    }
    rendered = render_notification("item/autoApprovalReview/completed", payload)
    assert rendered is not None and rendered.kind is EventKind.TOOL_RESULT
    assert rendered.text == "Auto-review denied: Deletes the repo\n"
    assert rendered.metadata == {"item_id": "exec-1"}
    assert render_notification("item/autoApprovalReview/started", payload) is None


def test_hook_completion_shows_only_unsuccessful_runs() -> None:
    run = {"eventName": "preToolUse", "status": "completed", "statusMessage": None}
    assert render_notification("hook/completed", {"run": run}) is None
    failed = render_notification(
        "hook/completed",
        {"run": {**run, "status": "blocked", "statusMessage": "policy"}},
    )
    assert failed is not None and failed.text == "Hook preToolUse blocked: policy"


def test_mcp_progress_is_a_tool_result_on_the_item() -> None:
    rendered = render_notification(
        "item/mcpToolCall/progress", {"itemId": "m1", "message": "50%"}
    )
    assert rendered is not None
    assert (rendered.kind, rendered.text) == (EventKind.TOOL_RESULT, "50%")


def test_ignored_methods_render_nothing() -> None:
    assert render_notification("thread/status/changed", {"status": "idle"}) is None
    assert render_notification("thread/compacted", {}) is None


def test_unknown_item_is_a_generic_tool_pair() -> None:
    item = {"type": "futureThing", "id": "x1", "detail": {"a": 1}}
    call = render_item_started(item)
    result = render_item_completed(item)
    assert call is not None and (call.kind, call.text) == (
        EventKind.TOOL_CALL,
        "futureThing",
    )
    assert result is not None and result.kind is EventKind.TOOL_RESULT
    assert result.text == '{"detail":{"a":1}}'
    assert extract_tool_name("futureThing", item) == "futureThing"


def test_unknown_item_body_is_bounded() -> None:
    item = {"type": "futureThing", "id": "x1", "blob": "y" * 10_000}
    result = render_item_completed(item)
    assert result is not None
    assert len(result.text.encode("utf-8")) <= UNKNOWN_ITEM_MAX_BYTES
    assert result.text.endswith("…")
