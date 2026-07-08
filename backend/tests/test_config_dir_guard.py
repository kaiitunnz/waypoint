"""Account-profile config-dir readiness guard.

Pointing ``CLAUDE_CONFIG_DIR`` at a dir whose ``.claude.json`` hasn't completed
onboarding relaunches the claude TUI into its first-run wizard, which a
tmux/tty-driven turn can't dismiss and so hangs. The runtime rejects such a
profile up front — but only for interactive-TUI transports (``has_terminal_pane``);
headless ``claude --print`` runs fine there and must not be blocked.
"""

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from waypoint.backends.base import ConfigDirNotReadyError
from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
from waypoint.backends.claude_code.threads import claude_onboarding_complete
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.runtime import SessionRuntime
from waypoint.settings import Settings
from waypoint.storage import Storage


def _onboarded(dir_: Path) -> str:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True}))
    return str(dir_)


def _unonboarded(dir_: Path) -> str:
    dir_.mkdir(parents=True, exist_ok=True)
    return str(dir_)


def _runtime(tmp_path: Path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    return SessionRuntime(settings, Storage(settings.database_path))


def _caps(runtime: SessionRuntime, transport: str):
    return runtime.registry.resolve("claude_code", transport).capabilities


# ── threads.claude_onboarding_complete ───────────────────────────────────────


def test_onboarding_complete_true(tmp_path: Path) -> None:
    assert claude_onboarding_complete(_onboarded(tmp_path / "cfg")) is True


def test_onboarding_complete_missing_file(tmp_path: Path) -> None:
    assert claude_onboarding_complete(_unonboarded(tmp_path / "cfg")) is False


def test_onboarding_complete_flag_absent(tmp_path: Path) -> None:
    d = tmp_path / "cfg"
    d.mkdir()
    (d / ".claude.json").write_text(json.dumps({"numStartups": 3}))
    assert claude_onboarding_complete(str(d)) is False


def test_onboarding_complete_flag_falsy(tmp_path: Path) -> None:
    d = tmp_path / "cfg"
    d.mkdir()
    (d / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": False}))
    assert claude_onboarding_complete(str(d)) is False


def test_onboarding_complete_malformed(tmp_path: Path) -> None:
    d = tmp_path / "cfg"
    d.mkdir()
    (d / ".claude.json").write_text("{ not json")
    assert claude_onboarding_complete(str(d)) is False


# ── ClaudeCodePlugin.ensure_config_dir_ready ─────────────────────────────────


def test_plugin_ensure_ready_passes(tmp_path: Path) -> None:
    ClaudeCodePlugin().ensure_config_dir_ready(_onboarded(tmp_path / "cfg"))


def test_plugin_ensure_ready_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigDirNotReadyError):
        ClaudeCodePlugin().ensure_config_dir_ready(_unonboarded(tmp_path / "cfg"))


# ── runtime._ensure_profile_config_dir_ready (the transport gate) ────────────


def test_guard_rejects_interactive_transport_when_unonboarded(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    cfg = _unonboarded(tmp_path / "cfg")
    with pytest.raises(HTTPException) as exc:
        runtime._ensure_profile_config_dir_ready(
            "claude_code",
            _caps(runtime, "claude_tty"),
            {"CLAUDE_CONFIG_DIR": cfg},
            "personal",
            None,
        )
    assert exc.value.status_code == 400
    assert "personal" in exc.value.detail
    assert "not set up" in exc.value.detail


def test_guard_allows_interactive_transport_when_onboarded(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    cfg = _onboarded(tmp_path / "cfg")
    runtime._ensure_profile_config_dir_ready(
        "claude_code",
        _caps(runtime, "claude_tty"),
        {"CLAUDE_CONFIG_DIR": cfg},
        "personal",
        None,
    )


def test_guard_exempts_headless_transport(tmp_path: Path) -> None:
    # claude --print does not onboard, so a native (no terminal pane) transport
    # must not be rejected even for an un-onboarded dir.
    runtime = _runtime(tmp_path)
    cfg = _unonboarded(tmp_path / "cfg")
    caps = _caps(runtime, "claude_cli")
    assert caps.has_terminal_pane is False
    runtime._ensure_profile_config_dir_ready(
        "claude_code", caps, {"CLAUDE_CONFIG_DIR": cfg}, "personal", None
    )


def test_guard_noop_without_profile(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime._ensure_profile_config_dir_ready(
        "claude_code", _caps(runtime, "claude_tty"), {}, None, None
    )


def test_guard_noop_on_remote_launch_target(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    cfg = _unonboarded(tmp_path / "cfg")
    target = SshLaunchTargetConfig(id="s0", name="s0", ssh_destination="s0")
    runtime._ensure_profile_config_dir_ready(
        "claude_code",
        _caps(runtime, "claude_tty"),
        {"CLAUDE_CONFIG_DIR": cfg},
        "personal",
        target,
    )


def test_guard_noop_for_agent_without_validation(tmp_path: Path) -> None:
    # codex has no onboarding hang (headless app-server fails fast); its agent
    # plugin isn't ConfigDirValidating, so the guard is a no-op even on an
    # interactive transport.
    runtime = _runtime(tmp_path)
    runtime._ensure_profile_config_dir_ready(
        "codex",
        _caps(runtime, "claude_tty"),
        {"CODEX_HOME": str(tmp_path / "nope")},
        "work",
        None,
    )
