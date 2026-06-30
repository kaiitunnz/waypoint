from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import BaseModel

from waypoint.backends import BackendCapabilities, BackendRegistry, get_registry
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
from waypoint.backends.registry import reset_registry_for_tests


@pytest.fixture(autouse=True)
def _reset_registry() -> Iterator[None]:
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


class _StubPlugin:
    """Minimal BackendPlugin Protocol implementation for registry tests.

    Spells out every method explicitly because ``runtime_checkable``
    Protocols enforce structural conformance via ``hasattr`` per
    declared attribute — ``__getattr__`` shortcuts don't satisfy the
    check on Python 3.12+.
    """

    id = "stub"
    transport_id = "stub-tr"
    label = "Stub"
    import_request_schema: type[BaseModel] | None = None
    config_schema: type[PluginConfig] = PluginConfig
    launch_target_schema: type[PluginLaunchTargetConfig] = PluginLaunchTargetConfig
    extra_env: dict[str, str] = {}
    capabilities = BackendCapabilities(is_structured=False, supports_resume=False)

    def transport_view(self, runtime: Any) -> Any:
        raise NotImplementedError

    def is_available_for_managed_launch(self, runtime: Any) -> bool:
        return True

    def remote_executable(self, launch_target: Any) -> str:
        return ""

    async def terminate_session(self, runtime: Any, session: Any) -> None:
        return None

    def native_thread_id(self, session: Any) -> str | None:
        return None

    def on_session_deleted(self, runtime: Any, session: Any) -> None:
        return None

    def validate_permission_mode(self, mode: str | None) -> str | None:
        return None

    async def apply_permission_mode(
        self, runtime: Any, session: Any, mode: str
    ) -> None:
        raise NotImplementedError

    async def apply_model(self, runtime: Any, session: Any, model: str | None) -> None:
        raise NotImplementedError

    async def apply_effort(
        self, runtime: Any, session: Any, effort: str | None
    ) -> bool:
        return False

    def effort_swap_message(self, effort: str | None) -> str:
        return ""

    async def list_models(
        self,
        runtime: Any,
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        return {}

    async def list_command_completions(
        self,
        runtime: Any,
        session: Any,
        *,
        trigger: str = "/",
        prefix: str = "",
        force_refresh: bool = False,
    ) -> list[Any]:
        return []

    async def restore_session(self, runtime: Any, session: Any) -> None:
        return None

    async def maybe_handle_input(self, runtime: Any, session: Any, request: Any) -> Any:
        return None

    async def answer_question(
        self,
        runtime: Any,
        session: Any,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> Any:
        raise NotImplementedError

    async def approve_plan(
        self,
        runtime: Any,
        session: Any,
        plan_item_id: str,
        decision: str,
        text: str | None,
    ) -> Any:
        raise NotImplementedError

    async def post_approval(self, runtime: Any, session: Any) -> None:
        return None

    async def fork_side_question(
        self,
        runtime: Any,
        session: Any,
        side_question_id: str,
        *,
        new_session_id: str,
        title: str,
        raw_log: Any,
        structured_log: Any,
    ) -> Any:
        raise NotImplementedError

    async def dismiss_side_question(
        self, runtime: Any, session: Any, side_question_id: str
    ) -> None:
        raise NotImplementedError

    def setup(self, runtime: Any) -> None:
        return None

    async def shutdown(self, runtime: Any) -> None:
        return None

    def create_context_usage_source(self, session: Any, runtime: Any) -> None:
        return None

    def register_routes(self, app: Any, context: Any) -> None:
        return None

    async def list_threads(
        self,
        runtime: Any,
        launch_target_id: str | None = None,
    ) -> list[Any]:
        return []

    async def import_thread(
        self, runtime: Any, request: Any, *, agent: str | None = None
    ) -> Any:
        raise NotImplementedError

    async def delete_thread(
        self,
        runtime: Any,
        thread_id: str,
        launch_target_id: str | None = None,
    ) -> bool:
        return False

    async def fork_session(
        self,
        runtime: Any,
        session: Any,
        new_session_id: str,
        title: str,
        raw_log: Any,
        structured_log: Any,
    ) -> Any:
        raise NotImplementedError

    async def create_session(self, runtime: Any, request: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def test_default_registry_has_legacy_plugins() -> None:
    registry = get_registry()
    assert registry.backends() == {
        "claude_code",
        "claude_tty",
        "codex",
        "opencode",
        "tmux",
    }
    assert registry.transports() == {
        "claude_cli",
        "claude_tty",
        "codex_app_server",
        "opencode_http",
        "tmux",
    }


def test_registry_lookup_by_transport() -> None:
    registry = get_registry()
    assert registry.for_transport("claude_cli").id == "claude_code"
    assert registry.for_transport("codex_app_server").id == "codex"
    assert registry.for_transport("opencode_http").id == "opencode"
    assert registry.for_transport("tmux").id == "tmux"


def test_resolve_keys_on_agent_and_transport() -> None:
    registry = get_registry()
    # Native pairs resolve to the agent's own plugin.
    assert registry.resolve("claude_code", "claude_cli").id == "claude_code"
    assert registry.resolve("codex", "codex_app_server").id == "codex"
    assert registry.resolve("opencode", "opencode_http").id == "opencode"
    assert registry.resolve("claude_tty", "claude_tty").id == "claude_tty"
    # A tmux-wrapped agent resolves to the tmux transport plugin, not the
    # agent — the agent is still carried on session.backend.
    assert registry.resolve("codex", "tmux").id == "tmux"
    assert registry.resolve("claude_code", "tmux").id == "tmux"
    assert registry.resolve("opencode", "tmux").id == "tmux"
    # claude_code declares the tty-tail transport, so the pair resolves to the
    # claude_tty driver while the row still records backend=claude_code.
    assert registry.resolve("claude_code", "claude_tty").id == "claude_tty"


def test_supported_transports_reports_agent_declaration() -> None:
    registry = get_registry()
    assert registry.supported_transports("claude_code") == (
        "claude_cli",
        "claude_tty",
        "tmux",
    )
    assert registry.supported_transports("codex") == ("codex_app_server", "tmux")


def test_resolve_falls_back_to_transport_owner_for_undeclared_pair() -> None:
    registry = get_registry()
    # claude_tty doesn't declare the tmux transport, but a legacy row pairing
    # the two must still resolve to the transport owner rather than raise.
    assert registry.resolve("claude_tty", "tmux").id == "tmux"
    with pytest.raises(KeyError):
        registry.resolve("codex", "unknown_transport")


def test_plugin_for_uses_both_backend_and_transport() -> None:
    from datetime import UTC, datetime

    from waypoint.schemas import SessionRecord, SessionSource, SessionStatus

    registry = get_registry()
    now = datetime.now(UTC)

    def _record(backend: str, transport: str) -> SessionRecord:
        return SessionRecord(
            id="x",
            backend=backend,
            source=SessionSource.MANAGED,
            title="t",
            cwd="/",
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="raw",
            structured_log_path="events",
            transport=transport,
        )

    # Same transport_id can't recur today, but the same agent over two
    # transports must dispatch to two different driver plugins.
    assert registry.plugin_for(_record("codex", "codex_app_server")).id == "codex"
    assert registry.plugin_for(_record("codex", "tmux")).id == "tmux"
    # A claude_code session driven over the tty-tail transport dispatches to
    # the claude_tty driver, not the structured claude_code adapter.
    assert registry.plugin_for(_record("claude_code", "claude_tty")).id == "claude_tty"


def test_registry_get_unknown_raises() -> None:
    registry = get_registry()
    with pytest.raises(KeyError):
        registry.get("unknown_backend")
    with pytest.raises(KeyError):
        registry.for_transport("unknown_transport")


def test_registry_capability_descriptors() -> None:
    registry = get_registry()
    cc = registry.get("claude_code").capabilities
    cdx = registry.get("codex").capabilities
    tmx = registry.get("tmux").capabilities
    for caps in (cc, cdx, tmx):
        assert isinstance(caps, BackendCapabilities)
    assert cc.is_structured and cdx.is_structured and not tmx.is_structured
    assert tmx.supports_resume and not cc.supports_resume and not cdx.supports_resume


def test_schema_field_rejects_unknown_backend() -> None:
    from datetime import UTC, datetime

    import pytest as pt

    from waypoint.schemas import SessionRecord, SessionSource, SessionStatus

    now = datetime.now(UTC)
    with pt.raises(Exception):  # noqa: B017 — pydantic raises ValidationError
        SessionRecord(
            id="x",
            backend="unknown_backend",  # not registered
            source=SessionSource.MANAGED,
            title="t",
            cwd="/",
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="raw",
            structured_log_path="events",
        )


def test_schema_field_accepts_registered_backend_string() -> None:
    from datetime import UTC, datetime

    from waypoint.schemas import SessionRecord, SessionSource, SessionStatus

    now = datetime.now(UTC)
    record = SessionRecord(
        id="x",
        backend="codex",
        source=SessionSource.MANAGED,
        title="t",
        cwd="/",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="raw",
        structured_log_path="events",
        transport="codex_app_server",
    )
    assert record.backend == "codex"
    assert record.transport == "codex_app_server"


def test_registry_rejects_duplicate_id() -> None:
    registry = BackendRegistry()
    registry.register(_StubPlugin())
    with pytest.raises(ValueError):
        registry.register(_StubPlugin())


def test_plugin_without_supported_transports_defaults_to_own() -> None:
    """A plugin that omits ``supported_transports`` (e.g. an older
    third-party plugin) resolves only over its own transport id."""
    registry = BackendRegistry()
    registry.register(_StubPlugin())
    assert registry.resolve("stub", "stub-tr").id == "stub"


def test_entry_point_plugins_are_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plugins published via the ``waypoint.backends`` entry-point
    group are discovered after the built-ins, so third-party packages
    can ship a backend without editing ``bootstrap.py``."""
    from waypoint.backends import bootstrap

    class External(_StubPlugin):
        id = "external"
        transport_id = "external-tr"
        label = "External"

    class FakeEntryPoint:
        name = "external"

        def load(self) -> Any:
            return External

    def fake_entry_points(*, group: str) -> list[FakeEntryPoint]:
        if group == bootstrap.ENTRY_POINT_GROUP:
            return [FakeEntryPoint()]
        return []

    monkeypatch.setattr(bootstrap, "entry_points", fake_entry_points)
    reset_registry_for_tests()
    registry = get_registry()
    assert "external" in registry.backends()
    assert registry.for_transport("external-tr").id == "external"


def test_entry_point_plugin_loader_failure_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken third-party plugin's ImportError must not take down
    the runtime — the bootstrap logs and skips it so the built-ins
    still register."""
    from waypoint.backends import bootstrap

    class BoomEntryPoint:
        name = "broken"

        def load(self) -> Any:
            raise ImportError("missing dependency in third-party plugin")

    def fake_entry_points(*, group: str) -> list[BoomEntryPoint]:
        if group == bootstrap.ENTRY_POINT_GROUP:
            return [BoomEntryPoint()]
        return []

    monkeypatch.setattr(bootstrap, "entry_points", fake_entry_points)
    reset_registry_for_tests()
    with caplog.at_level("ERROR", logger="waypoint.backends.bootstrap"):
        registry = get_registry()
    assert "broken" not in registry.backends()
    assert {"claude_code", "codex", "tmux"}.issubset(registry.backends())
    assert any("broken" in record.message for record in caplog.records)


def test_entry_point_plugin_factory_failure_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The factory call after ``ep.load()`` (e.g. ``build_plugin()``)
    must be guarded too. If the factory raises during plugin
    construction — invalid config, missing remote dependency, etc. —
    the bootstrap logs and skips the plugin instead of taking the
    runtime down."""
    from waypoint.backends import bootstrap

    def boom_factory() -> Any:
        raise RuntimeError("plugin construction blew up")

    class FactoryEntryPoint:
        name = "exploding-factory"

        def load(self) -> Any:
            # ``ep.load()`` succeeds (importing the module is fine);
            # the failure is in the callable it returns.
            return boom_factory

    def fake_entry_points(*, group: str) -> list[FactoryEntryPoint]:
        if group == bootstrap.ENTRY_POINT_GROUP:
            return [FactoryEntryPoint()]
        return []

    monkeypatch.setattr(bootstrap, "entry_points", fake_entry_points)
    reset_registry_for_tests()
    with caplog.at_level("ERROR", logger="waypoint.backends.bootstrap"):
        registry = get_registry()
    assert "exploding-factory" not in registry.backends()
    assert {"claude_code", "codex", "tmux"}.issubset(registry.backends())
    assert any("exploding-factory" in record.message for record in caplog.records)
