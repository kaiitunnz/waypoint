"""Tolerance for a codex CLI whose thread items are ahead of the pinned SDK.

Fixtures are real responses captured from codex 0.144.0. The SDK's own
``ReasoningEffort`` is an open ``str`` enum, so unknown efforts (``max``,
``ultra``) preserve their value without a shim; importing the codex package
installs the ``ThreadItem`` union tolerance.
"""

import inspect
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from openai_codex._message_router import MessageRouter, _TurnState
from openai_codex.client import CodexClient
from openai_codex.generated.notification_registry import NOTIFICATION_MODELS
from openai_codex.generated.v2_all import (
    ItemStartedNotification,
    ModelListResponse,
    ReasoningEffort,
    SubAgentActivityKind,
    ThreadReadResponse,
    ThreadResumeResponse,
    ThreadStartResponse,
    Turn,
)
from openai_codex.models import Notification, UnknownNotification
from pydantic import ValidationError

import waypoint.backends.codex  # noqa: F401  (installs the shim on import)
from waypoint.backends.codex._sdk_compat import (
    install_activity_kind_tolerance,
    install_thread_item_tolerance,
    start_compaction_turn,
)
from waypoint.backends.codex.adapter import CodexAppServerAdapter
from waypoint.backends.codex.plugin import CodexPlugin, CodexPluginConfig

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / f"{name}.json").read_text())


def test_model_list_with_unknown_efforts_validates_and_preserves_values() -> None:
    response = ModelListResponse.model_validate(_load("codex_model_list_0144"))
    efforts = {
        option.reasoning_effort.value
        for model in response.data
        for option in (model.supported_reasoning_efforts or [])
    }
    assert {"max", "ultra"} <= efforts


def test_thread_start_at_max_validates_and_preserves_effort() -> None:
    response = ThreadStartResponse.model_validate(_load("codex_thread_start_max_0144"))
    assert response.reasoning_effort is not None
    assert response.reasoning_effort.value == "max"


def test_unknown_effort_resolves_to_string_preserving_member() -> None:
    assert ReasoningEffort("max").value == "max"
    assert ReasoningEffort("ultra").value == "ultra"
    # A value not seen before is tolerated too (future CLI additions).
    assert ReasoningEffort("hyper").value == "hyper"


def test_unknown_effort_round_trips_through_json_serialization() -> None:
    response = ThreadStartResponse.model_validate(_load("codex_thread_start_max_0144"))
    dumped = response.model_dump(mode="json", by_alias=True)
    assert dumped["reasoningEffort"] == "max"


def test_concurrent_resolution_of_new_values_is_safe() -> None:
    values = [f"effort_{i}" for i in range(50)]
    resolved: dict[str, str] = {}

    def worker(value: str) -> None:
        resolved[value] = ReasoningEffort(value).value

    threads = [threading.Thread(target=worker, args=(v,)) for v in values]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert resolved == {value: value for value in values}


class _FakeSettings:
    default_cwd = "~"

    def plugin_config(self, _backend: str) -> CodexPluginConfig:
        return CodexPluginConfig()


class _FakeRuntime:
    settings = _FakeSettings()

    def _find_launch_target(self, _launch_target_id: str | None) -> None:
        return None

    def _resolve_launch_target(
        self, _launch_target_id: str | None, _backend: str
    ) -> None:
        return None

    async def discovery_env(
        self, _backend: str, _launch_target: Any, _account_profile_id: str | None
    ) -> dict[str, str]:
        return {}


@pytest.mark.asyncio
async def test_list_models_surfaces_unknown_efforts_through_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = CodexPlugin()
    plugin.adapter = CodexAppServerAdapter(emit_event=cast(Any, lambda *a, **k: None))
    response = ModelListResponse.model_validate(_load("codex_model_list_0144"))

    async def fake_adapter_list_models(
        _self: CodexAppServerAdapter,
        cwd: str = "~",
        client_factory_override: Any = None,
        include_hidden: bool = False,
    ) -> ModelListResponse:
        return response

    monkeypatch.setattr(plugin, "client_factory", lambda *a, **k: lambda *_a: None)
    monkeypatch.setattr(CodexAppServerAdapter, "list_models", fake_adapter_list_models)

    result = await plugin.list_models(cast(Any, _FakeRuntime()))
    efforts = {e for model in result["models"] for e in model["supported_efforts"]}
    assert {"max", "ultra"} <= efforts


# ── ThreadItem union tolerance ──────────────────────────────────────────────
# A codex CLI ahead of the pinned SDK emits thread item types the SDK's
# ``ThreadItem`` union does not model, which made ``thread/resume`` responses
# fail validation and reattach return 400. The synthetic type below stands in
# for any such not-yet-modeled item.

_UNKNOWN_ITEM = {
    "type": "waypointFutureItem",
    "id": "item-x1",
    "path": "/root/plan_review",
    "detail": {"nested": "kept"},
}
_KNOWN_ITEM = {"type": "reasoning", "id": "item-r1", "text": "thinking"}


def _thread_resume_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "approvalPolicy": "never",
        "approvalsReviewer": "user",
        "cwd": "/workspace",
        "model": "gpt-5-codex",
        "modelProvider": "openai",
        "sandbox": {"type": "readOnly"},
        "thread": {
            "cliVersion": "0.139.0",
            "createdAt": 1_700_000_000,
            "cwd": "/workspace",
            "ephemeral": False,
            "id": "th-1",
            "modelProvider": "openai",
            "preview": "hi",
            "sessionId": "sess-1",
            "source": "cli",
            "status": {"type": "idle"},
            "updatedAt": 1_700_000_010,
            "turns": [{"id": "turn-1", "status": "completed", "items": items}],
        },
    }


def test_thread_resume_with_unknown_item_validates_and_preserves() -> None:
    response = ThreadResumeResponse.model_validate(
        _thread_resume_payload([_KNOWN_ITEM, _UNKNOWN_ITEM])
    )
    known, unknown = response.thread.turns[0].items
    assert type(known.root).__name__ == "ReasoningThreadItem"
    assert type(unknown.root).__name__ == "UnknownThreadItem"
    # The whole unknown payload round-trips for downstream rendering.
    assert unknown.root.model_dump(by_alias=True) == _UNKNOWN_ITEM


def test_known_item_still_binds_to_strict_member() -> None:
    turn = Turn.model_validate(
        {"id": "t", "status": "completed", "items": [_KNOWN_ITEM]}
    )
    assert type(turn.items[0].root).__name__ == "ReasoningThreadItem"


def test_malformed_known_item_still_fails_loudly() -> None:
    # A known ``type`` with required fields missing must NOT be masked by the
    # unknown-item fallback; it should raise as before the shim.
    with pytest.raises(ValidationError):
        Turn.model_validate(
            {
                "id": "t",
                "status": "completed",
                "items": [{"type": "commandExecution", "id": "c"}],
            }
        )


def test_thread_item_tolerance_is_idempotent() -> None:
    # Re-invoking the installer is a no-op (guarded by a sentinel) and does not
    # corrupt the union.
    install_thread_item_tolerance()
    install_thread_item_tolerance()
    turn = Turn.model_validate(
        {"id": "t", "status": "completed", "items": [_UNKNOWN_ITEM]}
    )
    assert type(turn.items[0].root).__name__ == "UnknownThreadItem"


def test_unknown_item_in_live_notification_validates_and_unwraps() -> None:
    # ``item/started`` carries a ``ThreadItem`` and drives every live turn. After
    # widening, an unknown item validates into the typed notification (instead of
    # the SDK's UnknownNotification fallback); the item must still unwrap to the
    # same raw dict the normalizer consumes.
    notification = ItemStartedNotification.model_validate(
        {
            "itemId": "item-x1",
            "startedAtMs": 1,
            "threadId": "th-1",
            "turnId": "turn-1",
            "item": _UNKNOWN_ITEM,
        }
    )
    dumped = notification.model_dump(by_alias=True)
    assert dumped["item"] == _UNKNOWN_ITEM  # RootModel serializes as its root


def test_unknown_notification_method_still_falls_back_to_unknown() -> None:
    # The shim must not tighten the notification method dispatch: a genuinely
    # unknown method still has no model and degrades to UnknownNotification.
    assert "waypoint/nonexistent/method" not in NOTIFICATION_MODELS


# ── SubAgentActivityKind.completed tolerance ────────────────────────────────
# A codex CLI ahead of the pinned SDK persists ``subAgentActivity`` items with
# ``kind: "completed"`` -- a value the pinned closed enum
# (started/interacted/interrupted) lacks. Because the item *type* is modeled,
# the ThreadItem union tolerance above deliberately does not catch it, so the
# stale enum alone failed ``thread/resume`` validation and made reattach 400.

_COMPLETED_ACTIVITY = {
    "type": "subAgentActivity",
    "id": "item-subagent-1",
    "agentPath": "/root/note21_grounding",
    "agentThreadId": "thread-subagent-1",
    "kind": "completed",
}


def test_completed_activity_kind_resolves_and_preserves_value() -> None:
    assert SubAgentActivityKind("completed").value == "completed"


def test_thread_resume_with_completed_activity_validates_and_round_trips() -> None:
    # The exact response shape that raised ValidationError in production.
    response = ThreadResumeResponse.model_validate(
        _thread_resume_payload([_COMPLETED_ACTIVITY])
    )
    item = response.thread.turns[0].items[0]
    assert type(item.root).__name__ == "SubAgentActivityThreadItem"
    dumped = item.model_dump(mode="json", by_alias=True)
    assert dumped["kind"] == "completed"
    assert dumped["type"] == "subAgentActivity"


def test_completed_activity_validates_in_thread_read_history() -> None:
    # ThreadItem is embedded by every history-bearing response, not only resume.
    payload = _thread_resume_payload([_COMPLETED_ACTIVITY])
    response = ThreadReadResponse.model_validate({"thread": payload["thread"]})
    item = response.thread.turns[0].items[0]
    assert type(item.root).__name__ == "SubAgentActivityThreadItem"
    assert item.model_dump(mode="json", by_alias=True)["kind"] == "completed"


def test_completed_activity_validates_in_live_notification() -> None:
    # ``item/started`` carries a ThreadItem and drives every live turn.
    notification = ItemStartedNotification.model_validate(
        {
            "itemId": "item-subagent-1",
            "startedAtMs": 1,
            "threadId": "th-1",
            "turnId": "turn-1",
            "item": _COMPLETED_ACTIVITY,
        }
    )
    dumped = notification.model_dump(mode="json", by_alias=True)
    assert dumped["item"]["kind"] == "completed"


def test_unknown_activity_kind_still_fails_loudly() -> None:
    # Only ``completed`` is tolerated; other unmodeled kinds stay rejected so a
    # genuine schema mismatch is not silently accepted.
    bad = {**_COMPLETED_ACTIVITY, "kind": "paused"}
    with pytest.raises(ValidationError):
        Turn.model_validate({"id": "t", "status": "completed", "items": [bad]})
    for value in ("paused", "", "COMPLETED"):
        with pytest.raises(ValueError):
            SubAgentActivityKind(value)


def test_completed_activity_missing_required_field_still_fails() -> None:
    # The known item model stays strict: dropping a required field must fail,
    # not degrade to the unknown-item fallback.
    incomplete = {k: v for k, v in _COMPLETED_ACTIVITY.items() if k != "agentPath"}
    with pytest.raises(ValidationError):
        Turn.model_validate({"id": "t", "status": "completed", "items": [incomplete]})


def test_activity_kind_tolerance_is_idempotent() -> None:
    install_activity_kind_tolerance()
    install_activity_kind_tolerance()
    assert SubAgentActivityKind("completed").value == "completed"


def _routed(method: str, **params: Any) -> Notification:
    return Notification(method=method, payload=UnknownNotification(params=params))


class _CompactingClient:
    """The private client surface ``start_compaction_turn`` uses, backed by a
    real ``MessageRouter``; ``thread_compact`` routes what Codex would emit."""

    def __init__(self, emits_turn: bool = True) -> None:
        self._router = MessageRouter()
        self.emits_turn = emits_turn

    @contextmanager
    def _thread_start_lock(self, thread_id: str) -> Iterator[None]:
        yield

    def thread_compact(self, thread_id: str) -> dict[str, Any]:
        if self.emits_turn:
            route = self._router.route_notification
            route(_routed("turn/started", threadId=thread_id, turn={"id": "c1"}))
            item = {"type": "contextCompaction", "id": "cc1"}
            route(_routed("item/started", threadId=thread_id, turnId="c1", item=item))
        return {}

    def next_turn_notification(self, turn_id: str) -> Notification:
        return self._router.next_turn_notification(turn_id)


def test_start_compaction_turn_replays_the_buffered_turn() -> None:
    client = _CompactingClient()
    turn_id = start_compaction_turn(cast(CodexClient, client), "th1", timeout=1)
    assert turn_id == "c1"
    client._router.route_notification(
        _routed(
            "turn/completed", threadId="th1", turn={"id": "c1", "status": "completed"}
        )
    )
    methods = [client.next_turn_notification("c1").method for _ in range(3)]
    assert methods == ["turn/started", "item/started", "turn/completed"]


def test_start_compaction_turn_ignores_other_threads_and_times_out() -> None:
    client = _CompactingClient(emits_turn=False)
    client._router.route_notification(
        _routed("turn/started", threadId="other", turn={"id": "o1"})
    )
    assert start_compaction_turn(cast(CodexClient, client), "th1", timeout=0.1) is None


def test_start_compaction_turn_ignores_late_events_of_finished_turns() -> None:
    client = _CompactingClient(emits_turn=False)
    real_compact = client.thread_compact

    def compact_with_late_event(thread_id: str) -> dict[str, Any]:
        # A finished turn's trailing event recreates its buffer while pruning
        # is held; it must not be taken for the compaction turn.
        client._router.route_notification(
            _routed("item/completed", threadId=thread_id, turnId="old", item={})
        )
        return real_compact(thread_id)

    client.thread_compact = compact_with_late_event  # type: ignore[method-assign]
    assert start_compaction_turn(cast(CodexClient, client), "th1", timeout=0.1) is None


def test_start_compaction_turn_stops_when_cancelled() -> None:
    client = _CompactingClient(emits_turn=False)
    cancelled = threading.Event()
    cancelled.set()
    started = time.monotonic()
    turn_id = start_compaction_turn(
        cast(CodexClient, client), "th1", timeout=5, cancelled=cancelled
    )
    assert turn_id is None
    assert time.monotonic() - started < 1


def test_private_sdk_surface_for_compaction_exists() -> None:
    """Fails on an SDK bump that moves the seam ``start_compaction_turn`` uses."""
    client_attrs = {"_thread_start_lock", "thread_compact", "next_turn_notification"}
    for name in client_attrs:
        assert callable(getattr(CodexClient, name, None)), name
    router = MessageRouter()
    for name in ("pending_turn", "prepare_turn"):
        assert callable(getattr(router, name, None)), name
    assert isinstance(router._turn_states, dict)
    turn_state = _TurnState("t")
    assert isinstance(turn_state.events, dict)
    assert turn_state.first_event == 0
    assert hasattr(router._lock, "acquire")
    assert list(inspect.signature(router.prepare_turn).parameters) == [
        "turn_id",
        "thread_id",
        "cursors",
        "for_handle",
    ]
    assert "self._router = MessageRouter()" in inspect.getsource(CodexClient.__init__)


def test_notification_for_an_ended_turn_reaches_the_global_queue() -> None:
    router = MessageRouter()
    router.register_turn("t1")
    router.route_notification(
        _routed("turn/completed", threadId="th", turn={"id": "t1"})
    )
    router.unregister_turn("t1")

    late = _routed("item/completed", threadId="th", turnId="t1", item={})
    router.route_notification(late)

    assert router.next_global_notification() is late


def test_notification_for_a_live_turn_stays_on_the_turn() -> None:
    router = MessageRouter()
    router.register_turn("t1")
    live = _routed("item/completed", threadId="th", turnId="t1", item={})
    router.route_notification(live)

    assert router.next_turn_notification("t1") is live
    assert router._global_notifications.empty()


def test_ended_turn_of_a_goal_thread_keeps_sdk_routing() -> None:
    router = MessageRouter()
    router.route_notification(
        _routed("turn/completed", threadId="th", turn={"id": "t1"})
    )
    router._goal_operations["th"] = cast(
        Any,
        type(
            "Goal",
            (),
            {"observe": lambda self, n: True, "is_finished": lambda self: False},
        )(),
    )

    router.route_notification(
        _routed("item/completed", threadId="th", turnId="t1", item={})
    )

    assert router._global_notifications.empty()
