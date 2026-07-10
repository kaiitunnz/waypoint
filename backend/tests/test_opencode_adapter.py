import asyncio
import time
from typing import Any, cast

import pytest

from waypoint.backends.opencode.adapter import (
    OpenCodeAdapter,
    OpenCodeError,
    OpenCodeSessionState,
    _context_usage_snapshot_from_message,
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

        async def post(self, path, json_data=None, params=None, long_running=False):
            posted["path"] = path
            posted["json_data"] = json_data
            posted["long_running"] = long_running
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


@pytest.mark.asyncio
async def test_list_commands_reads_server_command_registry() -> None:
    adapter = _build_adapter()
    adapter._started = True
    state = OpenCodeSessionState(
        session_id="local-1",
        cwd="/tmp",
        opencode_session_id="ses_1",
    )
    adapter._register_session(state)

    class _FakeClient:
        async def get(self, path, params=None):
            assert path == "/command"
            assert params == {"directory": "/tmp"}
            return [{"name": "review", "description": "Review changes"}]

    adapter._client = _FakeClient()  # type: ignore[assignment]

    assert await adapter.list_commands("local-1") == [
        {"name": "review", "description": "Review changes"}
    ]


@pytest.mark.asyncio
async def test_execute_command_uses_native_session_command_endpoint() -> None:
    adapter = _build_adapter()
    state = OpenCodeSessionState(
        session_id="local-1",
        cwd="/tmp",
        opencode_session_id="ses_1",
        model="opencode/test-model",
        agent="build",
        effort="high",
    )
    adapter._register_session(state)
    posted: dict[str, object] = {}

    class _FakeClient:
        async def post(self, path, json_data=None, params=None, long_running=False):
            posted["path"] = path
            posted["json_data"] = json_data
            posted["long_running"] = long_running
            return {}

    adapter._client = _FakeClient()  # type: ignore[assignment]

    await adapter.execute_command("local-1", "review", "the auth changes")

    assert posted == {
        "path": "/session/ses_1/command",
        "json_data": {
            "command": "review",
            "arguments": "the auth changes",
            "model": "opencode/test-model",
            "agent": "build",
            "variant": "high",
        },
        "long_running": True,
    }


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


def test_context_usage_snapshot_aggregates_token_categories() -> None:
    snapshot = _context_usage_snapshot_from_message(
        "opencode",
        "opencode/minimax-m2.5-free",
        {
            "input": 120,
            "output": 30,
            "reasoning": 20,
            "cache": {"read": 15, "write": 5},
        },
        4096,
    )

    assert snapshot is not None
    assert snapshot.used_tokens == 140
    assert snapshot.context_window_tokens == 4096
    assert snapshot.source == "opencode"
    assert snapshot.breakdown == {
        "input_tokens": 120,
        "output_tokens": 30,
        "reasoning_tokens": 20,
        "cache_read_tokens": 15,
        "cache_write_tokens": 5,
    }


@pytest.mark.asyncio
async def test_context_usage_snapshot_deduplicates_cached_updates() -> None:
    calls: list[tuple[str, dict[str, object], bool]] = []

    async def _emit(*args: object, **kwargs: object) -> None:
        return None

    async def on_session_update(
        session_id: str, updates: dict[str, object], publish: bool
    ) -> object:
        calls.append((session_id, updates, publish))
        return None

    adapter = OpenCodeAdapter(
        emit_event=_emit,
        on_session_update=on_session_update,
    )
    state = OpenCodeSessionState(
        session_id="local-1",
        cwd="/tmp",
        opencode_session_id="ses_1",
    )
    state.context_window_by_model[("opencode", "opencode/minimax-m2.5-free")] = 4096

    properties = {
        "sessionID": "ses_1",
        "info": {
            "role": "assistant",
            "providerID": "opencode",
            "modelID": "opencode/minimax-m2.5-free",
            "tokens": {
                "input": 120,
                "output": 30,
                "reasoning": 20,
                "cache": {"read": 15, "write": 5},
            },
        },
    }

    await adapter._maybe_update_context_usage(state, properties)
    await adapter._maybe_update_context_usage(state, properties)

    assert len(calls) == 1
    assert calls[0][0] == "local-1"
    assert calls[0][2] is False
    context_usage = cast(dict[str, Any], calls[0][1]["context_usage"])
    assert context_usage["used_tokens"] == 140
    assert context_usage["context_window_tokens"] == 4096
    assert context_usage["source"] == "opencode"
    assert context_usage["breakdown"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "reasoning_tokens": 20,
        "cache_read_tokens": 15,
        "cache_write_tokens": 5,
    }


@pytest.mark.asyncio
async def test_token_usage_record_keys_on_session_and_message_id() -> None:
    records: list[tuple[str, Any, bool]] = []

    async def _emit(*args: object, **kwargs: object) -> None:
        return None

    async def on_token_usage(session_id: str, record: Any, publish: bool) -> object:
        records.append((session_id, record, publish))
        return None

    adapter = OpenCodeAdapter(emit_event=_emit, on_token_usage=on_token_usage)
    state = OpenCodeSessionState(
        session_id="local-1",
        cwd="/tmp",
        opencode_session_id="ses_1",
    )
    properties = {
        "sessionID": "ses_1",
        "info": {
            "id": "msg_9",
            "role": "assistant",
            "providerID": "opencode",
            "modelID": "opencode/minimax-m2.5-free",
            "tokens": {
                "input": 120,
                "output": 30,
                "reasoning": 20,
                "cache": {"read": 15, "write": 5},
            },
        },
    }

    # Published even though the context window is unknown (the ledger does not
    # need it) — decoupled from the context-usage snapshot path.
    await adapter._maybe_update_context_usage(state, properties)

    assert len(records) == 1
    session_id, record, _ = records[0]
    assert session_id == "local-1"
    assert record.record_id == "ses_1:msg_9"
    # reasoning is a subset of output for some providers, so no synthesized total.
    assert record.display_total_tokens is None
    assert record.totals == {
        "input_tokens": 120,
        "output_tokens": 30,
        "reasoning_tokens": 20,
        "cache_read_tokens": 15,
        "cache_write_tokens": 5,
    }


@pytest.mark.asyncio
async def test_context_usage_background_lookup_publishes_once_ready() -> None:
    calls: list[tuple[str, dict[str, object], bool]] = []

    async def _emit(*args: object, **kwargs: object) -> None:
        return None

    async def on_session_update(
        session_id: str, updates: dict[str, object], publish: bool
    ) -> object:
        calls.append((session_id, updates, publish))
        return None

    class _FakeClient:
        async def get(self, path, params=None):
            assert path == "/config/providers"
            return {
                "providers": [
                    {
                        "id": "opencode",
                        "models": {
                            "opencode/minimax-m2.5-free": {"limit": {"context": 4096}}
                        },
                    }
                ]
            }

    adapter = OpenCodeAdapter(
        emit_event=_emit,
        on_session_update=on_session_update,
    )
    adapter._client = _FakeClient()  # type: ignore[assignment]
    state = OpenCodeSessionState(
        session_id="local-1",
        cwd="/tmp",
        opencode_session_id="ses_1",
    )
    adapter._register_session(state)
    properties = {
        "sessionID": "ses_1",
        "info": {
            "role": "assistant",
            "providerID": "opencode",
            "modelID": "opencode/minimax-m2.5-free",
            "tokens": {
                "input": 120,
                "output": 30,
                "reasoning": 20,
                "cache": {"read": 15, "write": 5},
            },
        },
    }

    await adapter._maybe_update_context_usage(state, properties)

    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.01)

    assert len(calls) == 1
    assert calls[0][0] == "local-1"
    assert calls[0][2] is True
    context_usage = cast(dict[str, Any], calls[0][1]["context_usage"])
    assert context_usage["used_tokens"] == 140
    assert context_usage["context_window_tokens"] == 4096
    assert context_usage["source"] == "opencode"
    assert context_usage["breakdown"] == {
        "input_tokens": 120,
        "output_tokens": 30,
        "reasoning_tokens": 20,
        "cache_read_tokens": 15,
        "cache_write_tokens": 5,
    }
    assert (
        state.context_window_by_model[("opencode", "opencode/minimax-m2.5-free")]
        == 4096
    )
    assert (
        "opencode",
        "opencode/minimax-m2.5-free",
    ) not in state.context_usage_pending_tokens
    assert state.context_window_lookup_pending == set()
    assert not adapter._context_window_lookup_tasks


@pytest.mark.asyncio
async def test_context_window_lookup_failure_retries_after_ttl() -> None:
    calls: list[tuple[str, dict[str, object], bool]] = []

    async def _emit(*args: object, **kwargs: object) -> None:
        return None

    async def on_session_update(
        session_id: str, updates: dict[str, object], publish: bool
    ) -> object:
        calls.append((session_id, updates, publish))
        return None

    class _FakeClient:
        def __init__(self) -> None:
            self.fail_first = True

        async def get(self, path, params=None):
            assert path == "/config/providers"
            if self.fail_first:
                self.fail_first = False
                raise RuntimeError("temporary outage")
            return {
                "providers": [
                    {
                        "id": "opencode",
                        "models": {
                            "opencode/minimax-m2.5-free": {"limit": {"context": 4096}}
                        },
                    }
                ]
            }

    adapter = OpenCodeAdapter(
        emit_event=_emit,
        on_session_update=on_session_update,
    )
    client = _FakeClient()
    adapter._client = client  # type: ignore[assignment]
    state = OpenCodeSessionState(
        session_id="local-1",
        cwd="/tmp",
        opencode_session_id="ses_1",
    )
    adapter._register_session(state)
    key = ("opencode", "opencode/minimax-m2.5-free")
    properties = {
        "sessionID": "ses_1",
        "info": {
            "role": "assistant",
            "providerID": key[0],
            "modelID": key[1],
            "tokens": {
                "input": 120,
                "output": 30,
                "reasoning": 20,
                "cache": {"read": 15, "write": 5},
            },
        },
    }

    await adapter._maybe_update_context_usage(state, properties)
    for _ in range(100):
        if (
            key in state.context_window_lookup_failed
            and not state.context_window_lookup_pending
        ):
            break
        await asyncio.sleep(0.01)
    assert key in state.context_window_lookup_failed
    assert not calls

    state.context_window_lookup_failed[key] = time.monotonic() - 2 * 60.0
    await adapter._maybe_update_context_usage(state, properties)

    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.01)

    assert len(calls) == 1
    assert calls[0][2] is True
    assert state.context_window_by_model[key] == 4096


@pytest.mark.asyncio
async def test_delete_session_deletes_via_server_endpoint() -> None:
    adapter = _build_adapter()
    adapter._started = True

    class _FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def delete(self, path: str, params: Any = None) -> Any:
            self.calls.append(path)
            return True

    client = _FakeClient()
    adapter._client = cast(Any, client)

    assert await adapter.delete_session("ses_1") is True
    assert client.calls == ["/session/ses_1"]


@pytest.mark.asyncio
async def test_delete_session_treats_false_body_as_failure() -> None:
    adapter = _build_adapter()
    adapter._started = True

    class _FakeClient:
        async def delete(self, path: str, params: Any = None) -> Any:
            return False

    adapter._client = cast(Any, _FakeClient())
    assert await adapter.delete_session("ses_missing") is False


@pytest.mark.asyncio
async def test_delete_session_treats_empty_204_as_success() -> None:
    adapter = _build_adapter()
    adapter._started = True

    class _FakeClient:
        async def delete(self, path: str, params: Any = None) -> Any:
            return {}

    adapter._client = cast(Any, _FakeClient())
    assert await adapter.delete_session("ses_1") is True


@pytest.mark.asyncio
async def test_delete_session_swallows_http_error_as_failure() -> None:
    adapter = _build_adapter()
    adapter._started = True

    class _FakeClient:
        async def delete(self, path: str, params: Any = None) -> Any:
            raise RuntimeError("HTTP 404")

    adapter._client = cast(Any, _FakeClient())
    assert await adapter.delete_session("ses_404") is False
