"""Tests for Claude terminal appearance resolution.

Covers the pure classifier (``terminal_theme``) across the full source/precedence
matrix, the remote probe driver's failure modes, and plugin protocol delegation
(claude_code local path, claude_tty delegation, tmux generic inner delegation).
"""

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from waypoint.backends.base import TerminalAppearance
from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
from waypoint.backends.claude_code.terminal_theme import (
    classify_effective_appearance,
    classify_theme,
    read_theme_preference,
)
from waypoint.backends.claude_code.terminal_theme_remote import (
    probe_terminal_appearance_remote,
)
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
from waypoint.backends.tmux.plugin import TmuxPlugin
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.runtime import SessionRuntime
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus

_NOW = datetime(2026, 7, 11, tzinfo=UTC)


# ─── Pure classifier: built-in preferences ───


@pytest.mark.parametrize(
    "preference,expected",
    [
        ("light", "light"),
        ("light-ansi", "light"),
        ("light-daltonized", "light"),
        ("dark", "dark"),
        ("dark-ansi", "dark"),
        ("dark-daltonized", "dark"),
        ("auto", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
        ("nonsense", "unknown"),
        ("custom:plug:slug", "unknown"),  # plugin-contributed form
    ],
)
def test_classify_builtin(
    preference: str | None, expected: str, tmp_path: Path
) -> None:
    assert classify_theme(preference, tmp_path / "themes") == expected


# ─── Pure classifier: source precedence ───


def _write(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_precedence_local_over_shared_over_user(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cwd = tmp_path / "proj"
    _write(cfg / "settings.json", {"theme": "light"})
    _write(cwd / ".claude" / "settings.json", {"theme": "dark"})
    _write(cwd / ".claude" / "settings.local.json", {"theme": "light"})
    # local project wins
    assert read_theme_preference(str(cfg), str(cwd)) == "light"
    # remove local -> shared project wins
    (cwd / ".claude" / "settings.local.json").unlink()
    assert read_theme_preference(str(cfg), str(cwd)) == "dark"
    # remove shared -> user settings win
    (cwd / ".claude" / "settings.json").unlink()
    assert read_theme_preference(str(cfg), str(cwd)) == "light"


def test_legacy_claude_json_fallback(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    # no settings.json anywhere -> legacy <config_dir>/.claude.json
    _write(cfg / ".claude.json", {"theme": "dark-ansi"})
    assert classify_effective_appearance(str(cfg), None) == "dark"


def test_settings_json_beats_legacy(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    _write(cfg / "settings.json", {"theme": "light"})
    _write(cfg / ".claude.json", {"theme": "dark"})
    assert classify_effective_appearance(str(cfg), None) == "light"


def test_malformed_higher_precedence_does_not_mask_lower(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cwd = tmp_path / "proj"
    (cwd / ".claude").mkdir(parents=True)
    (cwd / ".claude" / "settings.local.json").write_text("{not json", encoding="utf-8")
    _write(cfg / "settings.json", {"theme": "light"})
    assert classify_effective_appearance(str(cfg), str(cwd)) == "light"


def test_default_home_when_no_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # settings.json under ~/.claude
    _write(home / ".claude" / "settings.json", {"theme": "light"})
    assert classify_effective_appearance(None, None) == "light"
    # legacy ~/.claude.json is in HOME, not ~/.claude
    (home / ".claude" / "settings.json").unlink()
    _write(home / ".claude.json", {"theme": "dark"})
    assert classify_effective_appearance(None, None) == "dark"


def test_no_cwd_skips_project_settings(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    _write(cfg / "settings.json", {"theme": "dark"})
    assert classify_effective_appearance(str(cfg), None) == "dark"


# ─── Pure classifier: custom themes ───


def test_custom_theme_base(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    _write(cfg / "themes" / "mine.json", {"base": "light"})
    _write(cfg / "settings.json", {"theme": "custom:mine"})
    assert classify_effective_appearance(str(cfg), None) == "light"


def test_custom_theme_omitted_base_is_dark(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    _write(cfg / "themes" / "mine.json", {})
    _write(cfg / "settings.json", {"theme": "custom:mine"})
    assert classify_effective_appearance(str(cfg), None) == "dark"


@pytest.mark.parametrize("base", ["mauve", "", 123, None, "custom:other", "auto"])
def test_custom_theme_bad_base_unknown(tmp_path: Path, base: Any) -> None:
    cfg = tmp_path / "cfg"
    _write(cfg / "themes" / "mine.json", {"base": base})
    assert classify_theme("custom:mine", cfg / "themes") == "unknown"


@pytest.mark.parametrize("slug", ["../evil", "a/b", "..", ".", "with space", ""])
def test_custom_theme_unsafe_slug_unknown(tmp_path: Path, slug: str) -> None:
    (tmp_path / "themes").mkdir()
    assert classify_theme(f"custom:{slug}", tmp_path / "themes") == "unknown"


def test_custom_theme_symlink_escape_unknown(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text('{"base":"light"}', encoding="utf-8")
    themes = tmp_path / "themes"
    themes.mkdir()
    link = themes / "evil.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unsupported")
    assert classify_theme("custom:evil", themes) == "unknown"


def test_custom_theme_oversize_unknown(tmp_path: Path) -> None:
    themes = tmp_path / "themes"
    themes.mkdir()
    (themes / "big.json").write_text(
        '{"base":"light","pad":"' + "x" * (64 * 1024) + '"}', encoding="utf-8"
    )
    assert classify_theme("custom:big", themes) == "unknown"


def test_custom_theme_missing_file_unknown(tmp_path: Path) -> None:
    (tmp_path / "themes").mkdir()
    assert classify_theme("custom:absent", tmp_path / "themes") == "unknown"


# ─── Remote probe: runs the canonical script standalone (no waypoint imports) ───


def test_remote_script_runs_standalone(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    _write(cfg / "settings.json", {"theme": "light"})
    script = (
        Path(__file__).resolve().parents[1]
        / "src/waypoint/backends/claude_code/terminal_theme.py"
    ).read_text()
    out = subprocess.run(
        [sys.executable, "-"],
        input=script,
        capture_output=True,
        text=True,
        env={"CLAUDE_CONFIG_DIR": str(cfg)},
    )
    assert out.returncode == 0
    assert json.loads(out.stdout.splitlines()[-1]) == {"appearance": "light"}


# ─── Remote probe driver: failure modes resolve to unknown ───


class _FakeTarget:
    def __init__(self, argv: list[str]) -> None:
        self._argv = argv
        self.captured_env: dict[str, str] = {}

    def build_remote_exec_args(
        self,
        command: list[str],
        cwd: str | None = None,
        *,
        extra_env: dict[str, str] | None = None,
    ) -> tuple[str, ...]:
        self.captured_env = dict(extra_env or {})
        return tuple(self._argv)


def _as_target(fake: _FakeTarget) -> SshLaunchTargetConfig:
    return cast(SshLaunchTargetConfig, fake)


def _as_runtime(fake: "_FakeRuntime") -> SessionRuntime:
    return cast(SessionRuntime, fake)


@pytest.mark.asyncio
async def test_remote_probe_light() -> None:
    target = _FakeTarget([sys.executable, "-c", "import sys; print(sys.stdin.read())"])
    # Feed a script on stdin that just echoes a light verdict.
    target._argv = [sys.executable, "-c", 'print(\'{"appearance":"light"}\')']
    result = await probe_terminal_appearance_remote(
        _as_target(target), "/work", launch_env={"CLAUDE_CONFIG_DIR": "/x"}
    )
    assert result == "light"
    assert target.captured_env["WAYPOINT_TERMINAL_THEME_CWD"] == "/work"
    assert target.captured_env["CLAUDE_CONFIG_DIR"] == "/x"


@pytest.mark.asyncio
async def test_remote_probe_nonzero_unknown() -> None:
    target = _FakeTarget([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert (
        await probe_terminal_appearance_remote(_as_target(target), None, launch_env={})
        == "unknown"
    )


@pytest.mark.asyncio
async def test_remote_probe_invalid_json_unknown() -> None:
    target = _FakeTarget([sys.executable, "-c", "print('garbage')"])
    assert (
        await probe_terminal_appearance_remote(_as_target(target), None, launch_env={})
        == "unknown"
    )


@pytest.mark.asyncio
async def test_remote_probe_missing_binary_unknown() -> None:
    target = _FakeTarget(["/nonexistent/python-binary-xyz"])
    assert (
        await probe_terminal_appearance_remote(_as_target(target), None, launch_env={})
        == "unknown"
    )


@pytest.mark.asyncio
async def test_remote_probe_timeout_unknown() -> None:
    target = _FakeTarget([sys.executable, "-c", "import time; time.sleep(5)"])
    assert (
        await probe_terminal_appearance_remote(
            _as_target(target), None, launch_env={}, timeout_seconds=0.3
        )
        == "unknown"
    )


# ─── Plugin protocol delegation ───


def _session(
    *,
    backend: str,
    transport: str,
    cwd: str,
    launch_env: dict[str, str] | None = None,
    launch_target_id: str | None = None,
) -> SessionRecord:
    return SessionRecord(
        id="s1",
        backend=backend,
        source=SessionSource.MANAGED,
        transport=transport,
        title="t",
        cwd=cwd,
        status=SessionStatus.IDLE,
        created_at=_NOW,
        updated_at=_NOW,
        last_event_at=_NOW,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/structured.log",
        launch_env=launch_env or {},
        launch_target_id=launch_target_id,
    )


class _FakeRuntime:
    def __init__(self, target: Any = None, blocked: bool = False) -> None:
        self._target = target
        self._blocked = blocked
        self.registry: Any = None

    def _find_launch_target(self, launch_target_id: str | None) -> Any:
        return self._target

    def remote_probe_blocked(self, launch_target_id: str | None) -> bool:
        return self._blocked


@pytest.mark.asyncio
async def test_claude_code_local_resolves(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    _write(cfg / "settings.json", {"theme": "light"})
    session = _session(
        backend="claude_code",
        transport="claude_cli",
        cwd=str(tmp_path),
        launch_env={"CLAUDE_CONFIG_DIR": str(cfg)},
    )
    plugin = ClaudeCodePlugin()
    result = await plugin.terminal_appearance(_as_runtime(_FakeRuntime()), session)
    assert result == TerminalAppearance.LIGHT


@pytest.mark.asyncio
async def test_claude_code_remote_blocked_is_unknown(tmp_path: Path) -> None:
    session = _session(
        backend="claude_code",
        transport="claude_cli",
        cwd=str(tmp_path),
        launch_target_id="remote-1",
    )
    plugin = ClaudeCodePlugin()
    runtime = _FakeRuntime(target=object(), blocked=True)
    result = await plugin.terminal_appearance(_as_runtime(runtime), session)
    assert result == TerminalAppearance.UNKNOWN


@pytest.mark.asyncio
async def test_claude_tty_delegates_to_claude(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    _write(cfg / "settings.json", {"theme": "dark"})
    session = _session(
        backend="claude_code",
        transport="claude_tty",
        cwd=str(tmp_path),
        launch_env={"CLAUDE_CONFIG_DIR": str(cfg)},
    )
    plugin = ClaudeTtyPlugin()
    result = await plugin.terminal_appearance(_as_runtime(_FakeRuntime()), session)
    assert result == TerminalAppearance.DARK


class _Registry:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def get(self, backend_id: str) -> Any:
        return self._inner


@pytest.mark.asyncio
async def test_tmux_delegates_to_inner_claude(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    _write(cfg / "settings.json", {"theme": "light"})
    session = _session(
        backend="claude_code",
        transport="tmux",
        cwd=str(tmp_path),
        launch_env={"CLAUDE_CONFIG_DIR": str(cfg)},
    )
    runtime = _FakeRuntime()
    runtime.registry = _Registry(ClaudeCodePlugin())
    result = await TmuxPlugin().terminal_appearance(_as_runtime(runtime), session)
    assert result == TerminalAppearance.LIGHT


@pytest.mark.asyncio
async def test_tmux_non_resolving_agent_is_unknown(tmp_path: Path) -> None:
    class _Bare:
        pass

    session = _session(backend="codex", transport="tmux", cwd=str(tmp_path))
    runtime = _FakeRuntime()
    runtime.registry = _Registry(_Bare())
    result = await TmuxPlugin().terminal_appearance(_as_runtime(runtime), session)
    assert result == TerminalAppearance.UNKNOWN


@pytest.mark.asyncio
async def test_tmux_bare_pane_is_unknown(tmp_path: Path) -> None:
    session = _session(backend="tmux", transport="tmux", cwd=str(tmp_path))
    result = await TmuxPlugin().terminal_appearance(
        _as_runtime(_FakeRuntime()), session
    )
    assert result == TerminalAppearance.UNKNOWN
