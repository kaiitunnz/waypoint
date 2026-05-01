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


def test_registry_rejects_duplicate_id() -> None:
    registry = BackendRegistry()

    class Stub:
        id = "stub"
        transport_id = "stub-tr"
        label = "Stub"
        capabilities = BackendCapabilities(is_structured=False, supports_resume=False)

        def transport_view(self, runtime):  # noqa: ANN001
            raise NotImplementedError

    registry.register(Stub())
    with pytest.raises(ValueError):
        registry.register(Stub())
