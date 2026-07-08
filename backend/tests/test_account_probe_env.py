"""Account-scoped probe env: probes read the account the session runs as.

Phase 3a. Rate-limit probes previously ran with the process env, so a session
under a non-default CLAUDE_CONFIG_DIR/CODEX_HOME (e.g. a switched account
profile) bucketed under the wrong account. ``account_lookup_env`` mirrors the
session's env and is threaded into the probes.
"""

from pathlib import Path
from typing import Any

import pytest

from waypoint.runtime import SessionRuntime
from waypoint.settings import Settings
from waypoint.storage import Storage


def _runtime(tmp_path: Path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    return SessionRuntime(settings, Storage(settings.database_path))


# ── account_lookup_env ──────────────────────────────────────────────────────


def test_account_lookup_env_overlays_launch_env_over_process_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    runtime = _runtime(tmp_path)
    env = runtime.account_lookup_env("codex", {"CODEX_HOME": "/x", "FOO": "bar"})
    # Session launch_env wins, process env (PATH) is present for the subprocess.
    assert env["CODEX_HOME"] == "/x"
    assert env["FOO"] == "bar"
    assert env["PATH"] == "/usr/bin"


def test_account_lookup_env_includes_extra_env_not_runtime_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate the env: WAYPOINT_SESSION_ID is set when the test itself runs
    # inside a Waypoint session (it's inherited from os.environ, not injected).
    monkeypatch.delenv("WAYPOINT_SESSION_ID", raising=False)
    runtime = _runtime(tmp_path)
    env = runtime.account_lookup_env("claude_code", {})
    # Backend extra_env is included (matches the launched process)...
    assert env.get("CLAUDE_CODE_NO_FLICKER") == "1"
    # ...but the helper never *injects* a session id (unlike _agent_process_env).
    assert "WAYPOINT_SESSION_ID" not in env


# ── probe_account_rate_limit threads the config-dir env ─────────────────────


async def test_claude_probe_uses_session_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    plugin: Any = runtime.registry.get("claude_code")
    captured: dict[str, Any] = {}

    async def fake_shared(*, env: Any = None, force: bool = False) -> None:
        captured["env"] = env
        return None

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.probe_claude_usage_shared", fake_shared
    )
    await plugin.probe_account_rate_limit(
        runtime, None, cwd="/x", launch_env={"CLAUDE_CONFIG_DIR": "/team"}
    )
    assert captured["env"]["CLAUDE_CONFIG_DIR"] == "/team"


async def test_claude_probe_without_launch_env_uses_process_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    plugin: Any = runtime.registry.get("claude_code")
    captured: dict[str, Any] = {}

    async def fake_shared(*, env: Any = None, force: bool = False) -> None:
        captured["env"] = env
        return None

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.probe_claude_usage_shared", fake_shared
    )
    await plugin.probe_account_rate_limit(runtime, None, cwd="/x")
    # No launch_env -> env stays None so the probe falls back to os.environ.
    assert captured["env"] is None


async def test_codex_probe_uses_session_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    plugin: Any = runtime.registry.get("codex")
    captured: dict[str, Any] = {}

    async def fake_status(
        *, cwd: str, binary: str, env: Any = None, timeout_seconds: float = 8.0
    ) -> None:
        captured["env"] = env
        return None

    monkeypatch.setattr(
        "waypoint.backends.codex.plugin.probe_codex_status", fake_status
    )
    await plugin.probe_account_rate_limit(
        runtime, None, cwd="/x", launch_env={"CODEX_HOME": "/work"}
    )
    assert captured["env"]["CODEX_HOME"] == "/work"
