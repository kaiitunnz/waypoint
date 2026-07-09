import shutil

from waypoint.backends.codex.adapter import _apply_codex_args

_REAL_BIN = shutil.which("env") or "/usr/bin/env"


def _deny(_method: str, _params: dict[str, object] | None) -> dict[str, object]:
    return {}


def test_local_bin_sets_codex_bin_without_cli_args() -> None:
    factory = _apply_codex_args(None, (), (), None, local_bin=_REAL_BIN)
    assert factory is not None
    client = factory("/tmp", _deny)
    assert client.config.codex_bin == _REAL_BIN
    assert client.config.launch_args_override is None


def test_local_bin_leads_argv_with_cli_args() -> None:
    factory = _apply_codex_args(None, ("--verbose",), (), None, local_bin=_REAL_BIN)
    assert factory is not None
    client = factory("/tmp", _deny)
    override = client.config.launch_args_override
    assert override is not None
    assert override[0] == _REAL_BIN
    assert "--verbose" in override
    assert override[-3:] == ("app-server", "--listen", "stdio://")


def test_unset_local_bin_with_no_args_returns_base() -> None:
    assert _apply_codex_args(None, (), (), None, local_bin=None) is None


def test_unset_local_bin_leaves_codex_bin_none_for_sdk_fallback() -> None:
    factory = _apply_codex_args(None, (), ('model="gpt-5"',), None, local_bin=None)
    assert factory is not None
    client = factory("/tmp", _deny)
    assert client.config.codex_bin is None


def test_remote_base_factory_is_returned_verbatim() -> None:
    sentinel = object()

    def base(_cwd: str, _handler: object) -> object:
        return sentinel

    factory = _apply_codex_args(base, ("--verbose",), (), None, local_bin=_REAL_BIN)  # type: ignore[arg-type]
    assert factory is base
