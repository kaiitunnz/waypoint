"""Unit tests for backends/claude_code/version.py's ``claude --version`` probe."""

from typing import Any

import pytest

from waypoint.backends.claude_code import version as version_module
from waypoint.backends.claude_code.version import (
    claude_cli_version_string,
    detect_claude_cli_version,
)
from waypoint.launch_targets import SshLaunchTargetConfig


@pytest.fixture(autouse=True)
def _clear_version_cache() -> None:
    # The TTL cache is keyed by binary path; clear it so one test's fake
    # subprocess result can't leak into the next.
    version_module._VERSION_STRING_CACHE.clear()


class _FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode


def _patch_run(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> Any:
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(version_module.subprocess, "run", fake_run)


def test_claude_cli_version_string_strips_trailing_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_run(monkeypatch, _FakeCompleted("2.1.197 (Claude Code)\n"))
    assert claude_cli_version_string("claude") == "2.1.197"


def test_detect_claude_cli_version_parses_labeled_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_run(monkeypatch, _FakeCompleted("2.1.197 (Claude Code)\n"))
    assert detect_claude_cli_version("claude") == (2, 1, 197)


def test_detect_claude_cli_version_parses_plain_semver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_run(monkeypatch, _FakeCompleted("2.1.5\n"))
    assert detect_claude_cli_version("claude") == (2, 1, 5)


def test_detect_claude_cli_version_none_on_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_run(monkeypatch, _FakeCompleted(""))
    assert detect_claude_cli_version("claude") is None


def test_detect_claude_cli_version_none_on_non_numeric_junk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_run(monkeypatch, _FakeCompleted("command not found"))
    # The raw string falls back to the first whitespace token (matching the
    # historical User-Agent behavior), but it doesn't parse as a version.
    assert claude_cli_version_string("claude") == "command"
    assert detect_claude_cli_version("claude") is None


def test_detect_claude_cli_version_none_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_run(monkeypatch, _FakeCompleted("2.1.197", returncode=1))
    assert detect_claude_cli_version("claude") is None


def test_detect_claude_cli_version_none_on_missing_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_run(monkeypatch, FileNotFoundError())
    assert detect_claude_cli_version("claude") is None


def test_detect_claude_cli_version_none_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    _patch_run(monkeypatch, subprocess.TimeoutExpired(cmd="claude", timeout=3))
    assert detect_claude_cli_version("claude") is None


def test_detect_claude_cli_version_short_circuits_for_remote_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        nonlocal called
        called = True
        return _FakeCompleted("2.1.197")

    monkeypatch.setattr(version_module.subprocess, "run", fake_run)
    target = SshLaunchTargetConfig(id="t", name="t", ssh_destination="remote")

    assert detect_claude_cli_version("claude", target) is None
    assert claude_cli_version_string("claude", target) is None
    assert not called


def test_detect_claude_cli_version_caches_within_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return _FakeCompleted("2.1.197")

    monkeypatch.setattr(version_module.subprocess, "run", fake_run)

    assert detect_claude_cli_version("claude") == (2, 1, 197)
    assert detect_claude_cli_version("claude") == (2, 1, 197)
    assert calls == 1
