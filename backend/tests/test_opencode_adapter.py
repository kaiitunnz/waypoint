import pytest

from waypoint.backends.opencode.adapter import (
    OpenCodeAdapter,
    OpenCodeError,
    OpenCodeSessionState,
)


def _build_adapter() -> OpenCodeAdapter:
    async def _emit(*args: object, **kwargs: object) -> None:
        return None

    return OpenCodeAdapter(emit_event=_emit)


def test_split_model_ref_handles_missing_or_malformed() -> None:
    adapter = _build_adapter()

    assert adapter._split_model_ref("opencode/minimax-m2.5-free") == {
        "providerID": "opencode",
        "modelID": "minimax-m2.5-free",
    }
    # A bare model id (no provider/) cannot be sent as the OpenCode model ref.
    assert adapter._split_model_ref("flat") is None
    assert adapter._split_model_ref("/no-provider") is None
    assert adapter._split_model_ref("no-model/") is None


def test_map_decision_to_reply_accepts_native_replies() -> None:
    adapter = _build_adapter()

    assert adapter._map_decision_to_reply("once") == "once"
    assert adapter._map_decision_to_reply("always") == "always"
    assert adapter._map_decision_to_reply("reject") == "reject"


def test_map_decision_to_reply_translates_aliases() -> None:
    adapter = _build_adapter()

    assert adapter._map_decision_to_reply("approve") == "once"
    assert adapter._map_decision_to_reply("accept") == "once"
    assert adapter._map_decision_to_reply("acceptForSession") == "always"
    assert adapter._map_decision_to_reply("decline") == "reject"
    assert adapter._map_decision_to_reply("deny") == "reject"


def test_map_decision_to_reply_rejects_unknown() -> None:
    adapter = _build_adapter()

    with pytest.raises(
        OpenCodeError, match="unsupported permission decision: surrender"
    ):
        adapter._map_decision_to_reply("surrender")


def test_extract_session_id_recurses_into_nested_payload() -> None:
    adapter = _build_adapter()

    assert adapter._extract_session_id({"sessionID": "ses_1"}) == "ses_1"
    assert adapter._extract_session_id({"info": {"sessionID": "ses_2"}}) == "ses_2"
    assert adapter._extract_session_id({"items": [{"sessionID": "ses_3"}]}) == "ses_3"
    assert adapter._extract_session_id({"unrelated": True}) is None


def test_tag_part_type_propagates_reasoning_to_subsequent_deltas() -> None:
    adapter = _build_adapter()
    state = OpenCodeSessionState(
        session_id="local-1",
        cwd="/tmp",
        opencode_session_id="ses_1",
    )

    # *-start arrives first carrying the part type.
    adapter._tag_part_type(
        state,
        "message.part.updated",
        {"sessionID": "ses_1", "part": {"id": "p_reason", "type": "reasoning"}},
    )
    adapter._tag_part_type(
        state,
        "message.part.updated",
        {"sessionID": "ses_1", "part": {"id": "p_text", "type": "text"}},
    )

    assert state.part_types == {"p_reason": "reasoning", "p_text": "text"}

    reasoning_delta = adapter._tag_part_type(
        state,
        "message.part.delta",
        {"partID": "p_reason", "field": "text", "delta": "thinking…"},
    )
    text_delta = adapter._tag_part_type(
        state,
        "message.part.delta",
        {"partID": "p_text", "field": "text", "delta": "answer"},
    )
    untracked_delta = adapter._tag_part_type(
        state,
        "message.part.delta",
        {"partID": "p_unknown", "field": "text", "delta": "?"},
    )

    assert reasoning_delta["_waypoint_part_type"] == "reasoning"
    assert text_delta["_waypoint_part_type"] == "text"
    assert "_waypoint_part_type" not in untracked_delta
