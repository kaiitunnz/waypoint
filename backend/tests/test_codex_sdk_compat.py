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
from openai_codex.generated.v2_all import (
    ModelListResponse,
    ReasoningEffort,
    ThreadStartResponse,
)

import waypoint.backends.codex  # noqa: F401  (installs the shim on import)
from waypoint.backends.codex._sdk_compat import install_reasoning_effort_tolerance
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
