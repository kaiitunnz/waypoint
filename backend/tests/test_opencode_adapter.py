import asyncio

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
    # No hardcoded fallback in the adapter — empty input returns None so the
    # prompt-without-model path lets OpenCode pick its own default.
    assert adapter._split_model_ref(None) is None
    assert adapter._split_model_ref("") is None


@pytest.mark.asyncio
async def test_compact_session_refetches_model_when_unpinned() -> None:
    adapter = _build_adapter()
    state = OpenCodeSessionState(
        session_id="local-1",
        cwd="/tmp",
        opencode_session_id="ses_1",
        model=None,
    )
    adapter._register_session(state)

    posted: dict[str, object] = {}

    class _FakeClient:
        async def get(self, path, params=None):
            assert path == "/session/ses_1"
            return {"model": "opencode/auto-default"}

        async def post(self, path, json_data=None, params=None):
            posted["path"] = path
            posted["json_data"] = json_data
            return {}

    adapter._client = _FakeClient()  # type: ignore[assignment]

    await adapter.compact_session("local-1")

    assert posted["path"] == "/session/ses_1/summarize"
    assert posted["json_data"] == {
        "providerID": "opencode",
        "modelID": "auto-default",
        "auto": False,
    }


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


def test_extract_session_id_consults_known_fields_only() -> None:
    adapter = _build_adapter()

    assert adapter._extract_session_id({"sessionID": "ses_1"}) == "ses_1"
    assert adapter._extract_session_id({"info": {"sessionID": "ses_2"}}) == "ses_2"
    assert adapter._extract_session_id({"part": {"sessionID": "ses_3"}}) == "ses_3"
    assert adapter._extract_session_id({"unrelated": True}) is None
    # An unrelated nested sessionID (e.g. inside tool metadata) must not
    # mis-route the event.
    assert adapter._extract_session_id({"metadata": {"sessionID": "ses_other"}}) is None


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


def test_resolve_state_for_part_delta_without_session_id() -> None:
    adapter = _build_adapter()
    state = OpenCodeSessionState(
        session_id="local-1",
        cwd="/tmp",
        opencode_session_id="ses_1",
    )
    adapter._register_session(state)
    adapter._tag_part_type(
        state,
        "message.part.updated",
        {"sessionID": "ses_1", "part": {"id": "p_text", "type": "text"}},
    )

    resolved = adapter._resolve_state_for_event(
        "message.part.delta",
        {"partID": "p_text", "field": "text", "delta": "answer"},
    )

    assert resolved is state


@pytest.mark.asyncio
async def test_dispatch_event_short_circuits_on_closing_state() -> None:
    adapter = _build_adapter()
    state = OpenCodeSessionState(
        session_id="local-1",
        cwd="/tmp",
        opencode_session_id="ses_1",
    )
    state.closing = True
    adapter._register_session(state)

    emitted: list[tuple[str, object]] = []

    async def _emit(*args: object, **kwargs: object) -> None:
        emitted.append(("emit", args))

    adapter._emit_event = _emit

    await adapter._dispatch_event(
        {
            "type": "message.part.updated",
            "properties": {
                "sessionID": "ses_1",
                "part": {"id": "p1", "type": "text"},
            },
        }
    )

    assert emitted == []
    # _tag_part_type should not have populated _part_sessions for a closing state
    assert adapter._part_sessions == {}


@pytest.mark.asyncio
async def test_listen_events_flushes_on_blank_sse_separator() -> None:
    adapter = _build_adapter()
    seen: list[dict[str, object]] = []

    class _FakeClient:
        async def stream_events(self, path: str):
            assert path == "/event"
            yield 'data: {"type":"server.connected","properties":{"sessionID":"ses_1"}}\n'
            yield "\n"
            await asyncio.sleep(1)

    async def _dispatch(event: dict[str, object]) -> None:
        seen.append(event)
        raise asyncio.CancelledError

    adapter._client = _FakeClient()  # type: ignore[assignment]
    adapter._dispatch_event = _dispatch  # type: ignore[method-assign]

    await adapter._listen_events()

    assert seen == [{"type": "server.connected", "properties": {"sessionID": "ses_1"}}]


@pytest.mark.asyncio
async def test_listen_events_treats_mid_session_eof_as_server_death() -> None:
    # Once we've delivered any event, a clean EOF must promote to
    # `_on_server_died` instead of silently retrying — OpenCode has no
    # SSE resume so any reconnect would skip events.
    adapter = _build_adapter()
    seen: list[dict[str, object]] = []
    died = asyncio.Event()

    class _FakeClient:
        async def stream_events(self, path: str):
            yield 'data: {"type":"server.connected","properties":{"sessionID":"ses_1"}}\n'
            yield "\n"
            return

    async def _dispatch(event: dict[str, object]) -> None:
        seen.append(event)

    async def _on_died() -> None:
        died.set()

    adapter._client = _FakeClient()  # type: ignore[assignment]
    adapter._dispatch_event = _dispatch  # type: ignore[method-assign]
    adapter._on_server_died = _on_died  # type: ignore[method-assign]

    await asyncio.wait_for(adapter._listen_events(), timeout=2)

    assert seen == [{"type": "server.connected", "properties": {"sessionID": "ses_1"}}]
    assert died.is_set()
