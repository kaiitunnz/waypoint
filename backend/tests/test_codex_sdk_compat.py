"""Tolerance for a codex CLI whose enums are ahead of the pinned SDK.

Fixtures are real responses captured from codex 0.144.0 (which advertises the
``max``/``ultra`` reasoning efforts the pinned SDK's ``ReasoningEffort`` enum does
not know). Importing the codex package installs the tolerance shim.
"""

import json
import threading
from pathlib import Path
from typing import Any, cast

import pytest
from openai_codex.generated.notification_registry import NOTIFICATION_MODELS
from openai_codex.generated.v2_all import (
    ItemStartedNotification,
    ModelListResponse,
    ReasoningEffort,
    ThreadResumeResponse,
    ThreadStartResponse,
    Turn,
)
from pydantic import ValidationError

import waypoint.backends.codex  # noqa: F401  (installs the shim on import)
from waypoint.backends.codex._sdk_compat import (
    install_reasoning_effort_tolerance,
    install_thread_item_tolerance,
)
from waypoint.backends.codex.adapter import CodexAppServerAdapter
from waypoint.backends.codex.plugin import CodexPlugin, CodexPluginConfig

_FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / f"{name}.json").read_text())


@pytest.fixture(autouse=True)
def _restore_reasoning_effort() -> Any:
    """Undo enum members fabricated by a test so the process-global enum
    doesn't leak throwaway values into later tests."""
    value_map = dict(ReasoningEffort._value2member_map_)
    member_map = dict(ReasoningEffort._member_map_)
    names = list(ReasoningEffort._member_names_)
    yield
    ReasoningEffort._value2member_map_.clear()
    ReasoningEffort._value2member_map_.update(value_map)
    ReasoningEffort._member_map_.clear()
    ReasoningEffort._member_map_.update(member_map)
    ReasoningEffort._member_names_[:] = names


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
    # Fabricated members are first-class: iteration surfaces them.
    assert ReasoningEffort("hyper") in list(ReasoningEffort)


def test_shim_is_idempotent_and_identity_stable() -> None:
    install_reasoning_effort_tolerance()
    install_reasoning_effort_tolerance()
    assert ReasoningEffort("max") is ReasoningEffort("max")


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
# ``ThreadItem`` union does not model (observed: ``subAgentActivity``), which made
# ``thread/resume`` responses fail validation and reattach return 400.

_UNKNOWN_ITEM = {
    "type": "subAgentActivity",
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
