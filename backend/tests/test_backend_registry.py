from collections.abc import Iterator

import pytest

from waypoint.backends import BackendCapabilities, BackendRegistry, get_registry
from waypoint.backends.registry import reset_registry_for_tests


@pytest.fixture(autouse=True)
def _reset_registry() -> Iterator[None]:
    reset_registry_for_tests()
    yield
    reset_registry_for_tests()


def test_default_registry_has_legacy_plugins() -> None:
    registry = get_registry()
    assert registry.backends() == {"claude_code", "codex", "tmux"}
    assert registry.transports() == {"claude_cli", "codex_app_server", "tmux"}


def test_registry_lookup_by_transport() -> None:
    registry = get_registry()
    assert registry.for_transport("claude_cli").id == "claude_code"
    assert registry.for_transport("codex_app_server").id == "codex"
    assert registry.for_transport("tmux").id == "tmux"


def test_registry_get_unknown_raises() -> None:
    registry = get_registry()
    with pytest.raises(KeyError):
        registry.get("opencode")
    with pytest.raises(KeyError):
        registry.for_transport("opencode_ws")


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
    with pt.raises(Exception):
        SessionRecord(
            id="x",
            backend="opencode",  # not registered
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
    from typing import Any

    registry = BackendRegistry()

    class Stub:
        id = "stub"
        transport_id = "stub-tr"
        label = "Stub"
        capabilities = BackendCapabilities(is_structured=False, supports_resume=False)

        def transport_view(self, runtime: Any) -> Any:
            raise NotImplementedError

        def validate_permission_mode(self, mode: str | None) -> str | None:
            return None

        async def apply_permission_mode(
            self, runtime: Any, session: Any, mode: str
        ) -> None:
            raise NotImplementedError

        async def apply_model(
            self, runtime: Any, session: Any, model: str | None
        ) -> None:
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

        async def restore_session(self, runtime: Any, session: Any) -> None:
            return None

    registry.register(Stub())
    with pytest.raises(ValueError):
        registry.register(Stub())
