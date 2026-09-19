"""Unit tests for the Claude task-notification parser and metadata builder."""

from datetime import UTC, datetime

from waypoint.backends.claude_code.normalize import (
    TASK_NOTIFICATION_INLINE_LIMIT,
    TASK_NOTIFICATION_ITEM_TYPE,
    TASK_NOTIFICATION_METHOD,
    build_task_notification_metadata,
    classify_injected_user_turn,
    infer_task_notification_kind,
    parse_task_notification,
)
from waypoint.schemas import SessionStatus

AGENT_COMPLETION = (
    "<task-notification>\n"
    "<task-id>a9af42717082ba876</task-id>\n"
    "<tool-use-id>toolu_01UkJg2Hc2uDxsGzrbju8ibo</tool-use-id>\n"
    "<output-file>/tmp/tasks/a9af42717082ba876.output</output-file>\n"
    "<status>completed</status>\n"
    '<summary>Agent "Map agent execution" finished</summary>\n'
    "<result>The full subagent report body.</result>\n"
    "<usage><subagent_tokens>107565</subagent_tokens>"
    "<tool_uses>21</tool_uses><duration_ms>160580</duration_ms></usage>\n"
    "</task-notification>"
)

MONITOR_EVENT = (
    "<task-notification>\n"
    "<task-id>bdtxigc6r</task-id>\n"
    '<summary>Monitor event: "e2e results"</summary>\n'
    "<event>FAIL: the traced workflow reached DONE</event>\n"
    "</task-notification>"
)

MONITOR_STREAM_ENDED = (
    "<task-notification>\n"
    "<task-id>b3gtbh2hh</task-id>\n"
    "<tool-use-id>toolu_013cZsFaYZt4PcNfUkP3nxJq</tool-use-id>\n"
    "<output-file>/tmp/tasks/b3gtbh2hh.output</output-file>\n"
    "<status>completed</status>\n"
    '<summary>Monitor "final suite result" stream ended</summary>\n'
    "<event>SUITE_DONE\nCompleted: 51 passed, 1 failed</event>\n"
    "</task-notification>"
)

BACKGROUND_COMPLETED = (
    "<task-notification>\n"
    "<task-id>bg0rg054d</task-id>\n"
    "<tool-use-id>toolu_01DzHKAdNUunhBXYreUq5VS5</tool-use-id>\n"
    "<output-file>/tmp/tasks/bg0rg054d.output</output-file>\n"
    "<status>completed</status>\n"
    '<summary>Background command "Wait for reply" completed (exit code 0)</summary>\n'
    "</task-notification>"
)

BACKGROUND_STOPPED = (
    "<task-notification>\n"
    "<task-id>b9nvvq0sl</task-id>\n"
    "<status>stopped</status>\n"
    "<summary>Background shell command didn't finish before the session ended</summary>\n"
    "<note>No completion record was found for it.</note>\n"
    "</task-notification>"
)


def _record(content: str, *, origin: bool = True, uuid: str = "rec-uuid") -> dict:
    record: dict = {"type": "user", "uuid": uuid, "message": {"content": content}}
    if origin:
        record["origin"] = {"kind": "task-notification"}
    return record


def test_classify_prefers_origin_then_content() -> None:
    assert (
        classify_injected_user_turn({"origin": {"kind": "task-notification"}}, "x")
        == "task_notification"
    )
    assert (
        classify_injected_user_turn({}, "  <task-notification>a</task-notification>")
        == "task_notification"
    )
    assert (
        classify_injected_user_turn({}, "This session is being continued from...")
        == "continuation"
    )
    assert classify_injected_user_turn({}, "hello") == "none"
    assert classify_injected_user_turn({}, [{"type": "text"}]) == "none"


def test_parse_agent_completion() -> None:
    parsed = parse_task_notification(AGENT_COMPLETION)
    assert parsed is not None
    assert parsed.task_id == "a9af42717082ba876"
    assert parsed.tool_use_id == "toolu_01UkJg2Hc2uDxsGzrbju8ibo"
    assert parsed.status == "completed"
    assert parsed.summary == 'Agent "Map agent execution" finished'
    assert parsed.result == "The full subagent report body."
    assert parsed.output_file == "/tmp/tasks/a9af42717082ba876.output"
    assert parsed.usage == {
        "subagent_tokens": 107565,
        "tool_uses": 21,
        "duration_ms": 160580,
    }
    assert infer_task_notification_kind(parsed) == "agent"


def test_parse_monitor_event_without_status_or_tool_use() -> None:
    parsed = parse_task_notification(MONITOR_EVENT)
    assert parsed is not None
    assert parsed.status is None
    assert parsed.tool_use_id is None
    assert parsed.output_file is None
    assert parsed.event == "FAIL: the traced workflow reached DONE"
    assert infer_task_notification_kind(parsed) == "monitor"


def _kind(content: str) -> str:
    parsed = parse_task_notification(content)
    assert parsed is not None
    return infer_task_notification_kind(parsed)


def test_kind_inference_across_shapes() -> None:
    assert _kind(MONITOR_STREAM_ENDED) == "monitor"
    assert _kind(BACKGROUND_COMPLETED) == "background_command"
    assert _kind(BACKGROUND_STOPPED) == "background_command"


def test_verbose_summary_does_not_misclassify_as_agent() -> None:
    # A background-command note whose paragraph mentions "agent teardown" and
    # "Monitor timeout" incidentally must still read as a background command.
    content = (
        "<task-notification><task-id>x</task-id><status>stopped</status>"
        "<summary>No completion record was found for this background shell "
        "command. It may have been stopped (via the UI, Monitor timeout, or "
        "agent teardown).</summary></task-notification>"
    )
    parsed = parse_task_notification(content)
    assert parsed is not None
    assert infer_task_notification_kind(parsed) == "background_command"


def test_parser_does_not_fabricate_scalars_from_result_body() -> None:
    # A result body that quotes XML-like tokens (subagent reviews do this) must
    # never fabricate a status/summary/event, and must be captured whole.
    content = (
        "<task-notification><task-id>x</task-id><summary>Real summary</summary>"
        "<result>quoting </result> then <status>FAKE</status> and "
        "<summary>FAKE</summary> inside</result></task-notification>"
    )
    parsed = parse_task_notification(content)
    assert parsed is not None
    assert parsed.summary == "Real summary"
    assert parsed.status is None
    assert parsed.event is None
    assert parsed.result == (
        "quoting </result> then <status>FAKE</status> and <summary>FAKE</summary> inside"
    )


def test_output_file_tag_inside_result_body_is_not_hoisted() -> None:
    # A report body that quotes an <output-file> tag must NOT be lifted into a
    # real output-file path — that would capture an arbitrary host file.
    content = (
        "<task-notification><task-id>x</task-id><status>completed</status>"
        '<summary>Agent "Review" finished</summary>'
        "<result>In my review I noted the field "
        "<output-file>/home/noppanat/.ssh/id_rsa</output-file> is parsed.</result>"
        "</task-notification>"
    )
    parsed = parse_task_notification(content)
    assert parsed is not None
    assert parsed.output_file is None
    assert "/home/noppanat/.ssh/id_rsa" in (parsed.result or "")
    _text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec", allow_output_capture=True
    )
    assert "capture_host_files" not in metadata
    assert "capture_host_text" not in metadata


def test_output_file_tag_inside_event_body_is_not_hoisted() -> None:
    # A monitor event's <event> can carry monitored-source text; an <output-file>
    # tag in it (on a notification with no real output-file) must not be hoisted.
    content = (
        "<task-notification><task-id>m</task-id>"
        '<summary>Monitor event: "watch"</summary>'
        "<event>log line: <output-file>/home/noppanat/.env</output-file> seen</event>"
        "</task-notification>"
    )
    parsed = parse_task_notification(content)
    assert parsed is not None
    assert parsed.output_file is None
    assert "/home/noppanat/.env" in (parsed.event or "")
    _text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec", allow_output_capture=True
    )
    assert "capture_host_files" not in metadata
    assert "capture_host_text" not in metadata


def test_usage_tag_inside_result_body_is_not_hoisted() -> None:
    content = (
        "<task-notification><task-id>x</task-id>"
        '<summary>Agent "X" finished</summary>'
        "<result>quoting <usage><subagent_tokens>999</subagent_tokens></usage> here</result>"
        "</task-notification>"
    )
    parsed = parse_task_notification(content)
    assert parsed is not None
    assert parsed.usage == {}
    assert "999" in (parsed.result or "")


def test_contentless_or_missing_wrapper_returns_none() -> None:
    assert (
        parse_task_notification("<task-notification>ping</task-notification>") is None
    )
    assert parse_task_notification("<task-notification></task-notification>") is None
    assert parse_task_notification("not a notification") is None
    assert parse_task_notification(None) is None
    assert parse_task_notification(["blocks"]) is None


def test_malformed_wrapper_does_not_raise() -> None:
    # Unterminated result, unknown tags, broken nesting: harmless, never raises.
    weird = (
        "<task-notification><status>completed</status><summary>ok</summary>"
        "<unknown-tag>?</unknown-tag><result>no close tag here"
    )
    parsed = parse_task_notification(weird)
    assert parsed is not None
    assert parsed.status == "completed"
    assert parsed.summary == "ok"


def test_html_entities_decoded() -> None:
    content = (
        "<task-notification><summary>A &amp; B &lt;x&gt;</summary>"
        "</task-notification>"
    )
    parsed = parse_task_notification(content)
    assert parsed is not None
    assert parsed.summary == "A & B <x>"


def test_build_metadata_agent_keeps_the_report_not_the_transcript() -> None:
    parsed = parse_task_notification(AGENT_COMPLETION)
    assert parsed is not None
    text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-1", allow_output_capture=True
    )
    assert text == 'Agent "Map agent execution" finished'
    assert metadata["method"] == TASK_NOTIFICATION_METHOD
    assert metadata["item_type"] == TASK_NOTIFICATION_ITEM_TYPE
    assert metadata["status"] == SessionStatus.RUNNING
    # An agent's output-file is its sidechain transcript, whose report already
    # rides inline; pinning hundreds of KB of tool churn buys nothing.
    assert "capture_host_files" not in metadata
    assert "capture_host_text" not in metadata
    payload = metadata["task_notification"]
    assert payload["version"] == 1
    assert payload["id"] == "rec-1"
    assert payload["kind"] == "agent"
    assert payload["status"] == "completed"
    # No separate artifact is promised: the report is the inline body.
    assert payload["output_available"] is False
    assert payload["output_unavailable_reason"] is None
    assert payload["output_unavailable_reason"] is None
    assert payload["result_preview"] == "The full subagent report body."
    assert payload["result_truncated"] is False
    assert payload["usage"]["tool_uses"] == 21


def test_build_metadata_monitor_text_appends_event() -> None:
    parsed = parse_task_notification(MONITOR_EVENT)
    assert parsed is not None
    text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-2", allow_output_capture=True
    )
    assert (
        text == 'Monitor event: "e2e results" — FAIL: the traced workflow reached DONE'
    )
    assert "capture_host_files" not in metadata
    assert "capture_host_text" not in metadata
    payload = metadata["task_notification"]
    assert payload["output_available"] is False
    assert payload["output_unavailable_reason"] is None


def test_build_metadata_import_skips_capture_with_reason() -> None:
    parsed = parse_task_notification(BACKGROUND_COMPLETED)
    assert parsed is not None
    _text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-3", allow_output_capture=False, ts=datetime.now(UTC)
    )
    assert "capture_host_files" not in metadata
    assert "capture_host_text" not in metadata
    payload = metadata["task_notification"]
    assert payload["output_available"] is False
    assert payload["output_unavailable_reason"] == "full output not captured on import"


def test_build_metadata_agent_import_claims_nothing_missing() -> None:
    # The report is inline, so an imported agent card has no absent artifact to
    # apologise for.
    parsed = parse_task_notification(AGENT_COMPLETION)
    assert parsed is not None
    _text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-3b", allow_output_capture=False, ts=datetime.now(UTC)
    )
    payload = metadata["task_notification"]
    assert payload["output_available"] is False
    assert payload["output_unavailable_reason"] is None
    assert payload["result_preview"] == "The full subagent report body."


def test_build_metadata_agent_without_a_report_still_captures() -> None:
    # Defensive: only the *inline report* makes the transcript redundant.
    content = AGENT_COMPLETION.replace(
        "<result>The full subagent report body.</result>\n", ""
    )
    parsed = parse_task_notification(content)
    assert parsed is not None
    _text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-3c", allow_output_capture=True
    )
    assert metadata["capture_host_text"] == ["/tmp/tasks/a9af42717082ba876.output"]
    assert metadata["task_notification"]["output_available"] is True


def test_build_metadata_oversized_inline_without_output_file() -> None:
    big = "x" * (TASK_NOTIFICATION_INLINE_LIMIT + 100)
    content = (
        "<task-notification><task-id>x</task-id><status>completed</status>"
        f'<summary>Agent "Big" finished</summary><result>{big}</result>'
        "</task-notification>"
    )
    parsed = parse_task_notification(content)
    assert parsed is not None
    _text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-4", allow_output_capture=True
    )
    assert "capture_host_files" not in metadata
    assert "capture_host_text" not in metadata
    payload = metadata["task_notification"]
    # Bounded preview inline; the full body spills to an attachment so the tail
    # survives even without an output-file.
    assert payload["result_truncated"] is True
    assert (
        len(payload["result_preview"].encode("utf-8")) <= TASK_NOTIFICATION_INLINE_LIMIT
    )
    assert payload["output_available"] is True
    assert payload["output_unavailable_reason"] is None
    spills = metadata["capture_inline_blobs"]
    assert [entry["filename"] for entry in spills] == ["task-rec-4-result.txt"]
    assert spills[0]["text"] == big


def test_build_metadata_stable_id_without_uuid_is_deterministic() -> None:
    parsed = parse_task_notification(MONITOR_EVENT)
    assert parsed is not None
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    _t1, m1 = build_task_notification_metadata(
        parsed, record_uuid=None, allow_output_capture=True, ts=ts
    )
    _t2, m2 = build_task_notification_metadata(
        parsed, record_uuid=None, allow_output_capture=True, ts=ts
    )
    assert m1["task_notification"]["id"] == m2["task_notification"]["id"]
    assert m1["task_notification"]["id"]


def test_build_metadata_bounds_event_and_note_and_spills_them() -> None:
    big_event = "e" * (TASK_NOTIFICATION_INLINE_LIMIT + 50)
    big_note = "n" * (TASK_NOTIFICATION_INLINE_LIMIT + 50)
    content = (
        "<task-notification><task-id>m9</task-id>"
        '<summary>Monitor event: "loud"</summary>'
        f"<event>{big_event}</event><note>{big_note}</note>"
        "</task-notification>"
    )
    parsed = parse_task_notification(content)
    assert parsed is not None
    text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-9", allow_output_capture=True
    )
    payload = metadata["task_notification"]
    assert payload["event_truncated"] is True
    assert payload["note_truncated"] is True
    for key in ("event", "note"):
        assert len(payload[key].encode("utf-8")) <= TASK_NOTIFICATION_INLINE_LIMIT
    # The event's own text is bounded too: it reaches SQLite and every client.
    assert len(text.encode("utf-8")) <= TASK_NOTIFICATION_INLINE_LIMIT + 1024
    spills = {
        entry["filename"]: entry["text"] for entry in metadata["capture_inline_blobs"]
    }
    assert spills == {
        "task-rec-9-event.txt": big_event,
        "task-rec-9-note.txt": big_note,
    }


def test_build_metadata_bounds_a_huge_summary_without_spilling_it() -> None:
    big = "s" * 20_000
    content = (
        f"<task-notification><task-id>s1</task-id><summary>{big}</summary>"
        "<event>ok</event></task-notification>"
    )
    parsed = parse_task_notification(content)
    assert parsed is not None
    _text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-s", allow_output_capture=True
    )
    payload = metadata["task_notification"]
    assert len(payload["summary"]) < len(big)
    assert "capture_inline_blobs" not in metadata


def test_build_metadata_omits_the_spill_key_when_nothing_overflows() -> None:
    parsed = parse_task_notification(MONITOR_EVENT)
    assert parsed is not None
    _text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-s", allow_output_capture=True
    )
    assert "capture_inline_blobs" not in metadata


def test_build_metadata_capture_disabled_keeps_bounded_text_and_says_why() -> None:
    big = "x" * (TASK_NOTIFICATION_INLINE_LIMIT + 100)
    content = (
        "<task-notification><task-id>x</task-id><status>completed</status>"
        f'<summary>Agent "Big" finished</summary><result>{big}</result>'
        "</task-notification>"
    )
    parsed = parse_task_notification(content)
    assert parsed is not None
    _text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-d", allow_output_capture=True, capture_enabled=False
    )
    payload = metadata["task_notification"]
    assert "capture_inline_blobs" not in metadata
    assert payload["result_truncated"] is True
    assert (
        len(payload["result_preview"].encode("utf-8")) <= TASK_NOTIFICATION_INLINE_LIMIT
    )
    assert payload["output_available"] is False
    assert payload["output_unavailable_reason"] == "output capture is disabled"


def test_build_metadata_capture_disabled_does_not_claim_an_import() -> None:
    parsed = parse_task_notification(BACKGROUND_COMPLETED)
    assert parsed is not None
    _text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-d2", allow_output_capture=True, capture_enabled=False
    )
    payload = metadata["task_notification"]
    assert "capture_host_files" not in metadata
    assert "capture_host_text" not in metadata
    # A live session with capture off must not be explained as an import.
    assert payload["output_unavailable_reason"] == "output capture is disabled"


def test_build_metadata_import_still_reports_an_import() -> None:
    parsed = parse_task_notification(BACKGROUND_COMPLETED)
    assert parsed is not None
    _text, metadata = build_task_notification_metadata(
        parsed, record_uuid="rec-i", allow_output_capture=False, capture_enabled=True
    )
    payload = metadata["task_notification"]
    assert "capture_host_files" not in metadata
    assert "capture_host_text" not in metadata
    assert "capture_inline_blobs" not in metadata
    assert payload["output_unavailable_reason"] == "full output not captured on import"
