"""Account-scoped probe env: probes read the account the session runs as.

Phase 3a. Rate-limit probes previously ran with the process env, so a session
under a non-default CLAUDE_CONFIG_DIR/CODEX_HOME (e.g. a switched account
profile) bucketed under the wrong account. ``account_lookup_env`` mirrors the
session's env and is threaded into the probes.

Phase 3b (goal 3, remote): the remote probe path dropped ``launch_env``
entirely and always probed the launch target's default account, so a remote
profile switch had no way to verify the *selected* profile's account before
switching. ``launch_env`` is now threaded through the remote probe helpers and
the shared remote probe cache is keyed by ``(target_id, config_dir)`` instead
of target id alone.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.claude_code import rate_limits as claude_rate_limits
from waypoint.backends.codex import rate_limits as codex_rate_limits
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.runtime import SessionRuntime
from waypoint.schemas import SessionRateLimitUsage
from waypoint.settings import Settings
from waypoint.storage import Storage


def _runtime(tmp_path: Path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    return SessionRuntime(settings, Storage(settings.database_path))


def _ssh_target(target_id: str = "rover") -> SshLaunchTargetConfig:
    return SshLaunchTargetConfig(
        id=target_id,
        name=target_id,
        ssh_destination="user@rover.lan",
        ssh_args=[],
        remote_shell="",
    )


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


# ── remote probe threads launch_env (goal 3) ─────────────────────────────────


async def test_claude_probe_remote_threads_launch_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    plugin: Any = runtime.registry.get("claude_code")
    target = _ssh_target()
    captured: dict[str, Any] = {}

    async def fake_remote_shared(
        launch_target: Any, *, launch_env: Any = None, force: bool = False
    ) -> None:
        captured["launch_target"] = launch_target
        captured["launch_env"] = launch_env
        return None

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.probe_claude_usage_remote_shared",
        fake_remote_shared,
    )
    await plugin.probe_account_rate_limit(
        runtime, target, cwd="/x", launch_env={"CLAUDE_CONFIG_DIR": "/team/acct-a"}
    )
    assert captured["launch_target"] is target
    assert captured["launch_env"]["CLAUDE_CONFIG_DIR"] == "/team/acct-a"


async def test_codex_probe_remote_threads_launch_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    plugin: Any = runtime.registry.get("codex")
    target = _ssh_target()
    captured: dict[str, Any] = {}

    async def fake_remote(
        launch_target: Any, *, binary: str = "codex", launch_env: Any = None
    ) -> None:
        captured["launch_target"] = launch_target
        captured["launch_env"] = launch_env
        return None

    monkeypatch.setattr(
        "waypoint.backends.codex.plugin.probe_codex_usage_remote", fake_remote
    )
    await plugin.probe_account_rate_limit(
        runtime, target, cwd="/x", launch_env={"CODEX_HOME": "/team/acct-b"}
    )
    assert captured["launch_target"] is target
    assert captured["launch_env"]["CODEX_HOME"] == "/team/acct-b"


# ── remote probe command carries the profile's config dir via extra_env ────


async def test_claude_remote_probe_command_carries_config_dir_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _ssh_target()
    captured: dict[str, Any] = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self, data: bytes) -> tuple[bytes, bytes]:
            return b'{"error": "no_credentials"}\n', b""

    async def fake_exec(*argv: str, **kwargs: Any) -> _FakeProc:
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await claude_rate_limits.probe_claude_usage_remote(
        target, launch_env={"CLAUDE_CONFIG_DIR": "/team/acct-a"}
    )
    remote_command = captured["argv"][-1]
    assert "CLAUDE_CONFIG_DIR=/team/acct-a" in remote_command


async def test_codex_remote_probe_command_carries_config_dir_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _ssh_target()
    captured: dict[str, Any] = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self, data: bytes) -> tuple[bytes, bytes]:
            return b'{"error": "no_data"}\n', b""

    async def fake_exec(*argv: str, **kwargs: Any) -> _FakeProc:
        captured["argv"] = argv
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    await codex_rate_limits.probe_codex_usage_remote(
        target, binary="codex", launch_env={"CODEX_HOME": "/team/acct-b"}
    )
    remote_command = captured["argv"][-1]
    assert "CODEX_HOME=/team/acct-b" in remote_command


# ── shared remote probe cache is keyed by (target_id, config_dir) ──────────


async def test_claude_remote_shared_cache_distinguishes_config_dirs_on_one_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _ssh_target()
    calls: list[str | None] = []

    async def fake_probe(
        launch_target: Any, *, launch_env: Any = None, timeout_seconds: float = 30.0
    ) -> SessionRateLimitUsage:
        calls.append((launch_env or {}).get("CLAUDE_CONFIG_DIR"))
        return SessionRateLimitUsage(source="claude_code", updated_at=datetime.now(UTC))

    monkeypatch.setattr(claude_rate_limits, "probe_claude_usage_remote", fake_probe)
    monkeypatch.setattr(
        claude_rate_limits,
        "_SHARED_PROBE_CACHE",
        claude_rate_limits.SharedRateLimitProbeCache(),
    )

    await claude_rate_limits.probe_claude_usage_remote_shared(
        target, launch_env={"CLAUDE_CONFIG_DIR": "/team/acct-a"}
    )
    await claude_rate_limits.probe_claude_usage_remote_shared(
        target, launch_env={"CLAUDE_CONFIG_DIR": "/team/acct-b"}
    )
    # Repeats a config_dir already probed above: served from cache, no new call.
    await claude_rate_limits.probe_claude_usage_remote_shared(
        target, launch_env={"CLAUDE_CONFIG_DIR": "/team/acct-a"}
    )

    assert calls == ["/team/acct-a", "/team/acct-b"]
