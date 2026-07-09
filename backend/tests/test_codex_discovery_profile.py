"""Unit tests for codex list_threads/list_models CODEX_HOME profile scoping.

``delete_thread`` scoping is covered in ``test_codex_thread_delete.py``. These
tests cover the live-RPC paths (list_threads, list_models) whose local client
factory previously dropped ``launch_env``, so a selected account profile's
CODEX_HOME never reached the app-server subprocess.
"""

from typing import Any, cast

import pytest

from waypoint.backends.codex.adapter import CodexAppServerAdapter
from waypoint.backends.codex.plugin import CodexPlugin, CodexPluginConfig
from waypoint.launch_targets import SshLaunchTargetConfig


class _FakeThreadListResponse:
    def __init__(self, data: list[Any], next_cursor: str | None = None) -> None:
        self.data = data
        self.next_cursor = next_cursor


class _FakeClient:
    """Stands in for ``CodexClient``: start/initialize/close are no-ops."""

    def __init__(self, thread_list_response: _FakeThreadListResponse) -> None:
        self._thread_list_response = thread_list_response

    def start(self) -> None:
        return None

    def initialize(self) -> None:
        return None

    def thread_list(self, _params: dict[str, Any]) -> _FakeThreadListResponse:
        return self._thread_list_response

    def close(self) -> None:
        return None


class _FakeStorage:
    def list_sessions(self) -> list[Any]:
        return []


class _FakeSettings:
    default_cwd = "~"

    def plugin_config(self, _backend: str) -> CodexPluginConfig:
        return CodexPluginConfig()


class _FakeRuntime:
    """A minimal runtime stub exposing only what discovery methods touch."""

    def __init__(self, discovery_env: dict[str, str]) -> None:
        self.storage = _FakeStorage()
        self.settings = _FakeSettings()
        self._discovery_env = discovery_env

    def _resolve_launch_target(
        self, _launch_target_id: str | None, _backend: str
    ) -> SshLaunchTargetConfig | None:
        return None

    def _find_launch_target(
        self, _launch_target_id: str | None
    ) -> SshLaunchTargetConfig | None:
        return None

    async def discovery_env(
        self,
        _backend: str,
        _launch_target: SshLaunchTargetConfig | None,
        _account_profile_id: str | None,
    ) -> dict[str, str]:
        return self._discovery_env


@pytest.mark.asyncio
async def test_list_threads_routes_profile_env_into_client_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = CodexPlugin()
    captured: dict[str, Any] = {}
    fake_client = _FakeClient(_FakeThreadListResponse([]))

    def fake_client_factory(
        runtime: Any,
        launch_target_id: str | None,
        custom_args: list[str] | None = None,
        custom_config_overrides: list[str] | None = None,
        launch_env: dict[str, str] | None = None,
    ) -> Any:
        captured["launch_env"] = launch_env
        return lambda cwd, approval_handler: fake_client

    monkeypatch.setattr(plugin, "client_factory", fake_client_factory)

    runtime = _FakeRuntime({"CODEX_HOME": "/fake/profile/home"})
    threads = await plugin.list_threads(cast(Any, runtime), account_profile_id="acct-1")

    assert threads == []
    assert captured["launch_env"] == {"CODEX_HOME": "/fake/profile/home"}


@pytest.mark.asyncio
async def test_list_models_routes_profile_env_into_client_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = CodexPlugin()
    plugin.adapter = CodexAppServerAdapter(
        emit_event=cast(Any, lambda *_args, **_kwargs: None),
    )
    captured: dict[str, Any] = {}

    def fake_client_factory(
        runtime: Any,
        launch_target_id: str | None,
        custom_args: list[str] | None = None,
        custom_config_overrides: list[str] | None = None,
        launch_env: dict[str, str] | None = None,
    ) -> Any:
        # The returned override is what actually carries the profile's env to
        # the live RPC; a truthy sentinel exercises adapter.list_models'
        # "override provided" branch instead of falling back to its default.
        captured["launch_env"] = launch_env
        return lambda cwd, approval_handler: None

    monkeypatch.setattr(plugin, "client_factory", fake_client_factory)

    class _FakeModelListResponse:
        data: list[Any] = []

    async def fake_adapter_list_models(
        self: CodexAppServerAdapter,
        cwd: str = "~",
        client_factory_override: Any = None,
        include_hidden: bool = False,
    ) -> _FakeModelListResponse:
        assert client_factory_override is not None
        return _FakeModelListResponse()

    monkeypatch.setattr(CodexAppServerAdapter, "list_models", fake_adapter_list_models)

    runtime = _FakeRuntime({"CODEX_HOME": "/fake/profile/home"})
    result = await plugin.list_models(cast(Any, runtime), account_profile_id="acct-1")

    assert result["models"] == []
    assert captured["launch_env"] == {"CODEX_HOME": "/fake/profile/home"}
