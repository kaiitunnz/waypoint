import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from agent_cli_guard import AgentCliGuard, real_agent_cli
from waypoint.backends.codex import plugin as codex_plugin
from waypoint.runtime import SessionRuntime
from waypoint.settings import Settings
from waypoint.storage import Storage


def _fake_cli(directory: Path, name: str = "codex") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / "ran"
    binary = directory / name
    binary.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    binary.chmod(0o755)
    return binary


def test_flags_agent_cli_outside_allowed_roots(tmp_path: Path) -> None:
    binary = _fake_cli(tmp_path / "bin")

    assert real_agent_cli([str(binary), "-s"], {}, []) == str(binary.resolve())
    assert real_agent_cli([str(binary), "-s"], {}, [tmp_path]) is None


def test_resolves_bare_name_on_the_spawn_env_path(tmp_path: Path) -> None:
    binary = _fake_cli(tmp_path / "bin", "claude")
    env = {"PATH": str(binary.parent)}

    assert real_agent_cli(["claude", "-p"], {"env": env}, []) == str(binary.resolve())
    assert real_agent_cli("claude", {"env": env}, []) == str(binary.resolve())
    empty_path = {"PATH": str(tmp_path / "empty")}
    assert real_agent_cli(["claude"], {"env": empty_path}, []) is None


def test_resolves_relative_path_against_spawn_cwd(tmp_path: Path) -> None:
    binary = _fake_cli(tmp_path / "bin", "opencode")

    assert real_agent_cli(["./opencode"], {"cwd": str(binary.parent)}, []) == str(
        binary.resolve()
    )


def test_ignores_other_programs_missing_binaries_and_shell(tmp_path: Path) -> None:
    _fake_cli(tmp_path / "bin")

    assert real_agent_cli(["git", "status"], {}, []) is None
    assert real_agent_cli([str(tmp_path / "missing" / "codex")], {}, []) is None
    assert real_agent_cli(f"{tmp_path}/bin/codex", {"shell": True}, []) is None


def test_guard_blocks_and_records_the_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _fake_cli(tmp_path / "bin")
    guard = AgentCliGuard([])
    guard.install(monkeypatch)

    with pytest.raises(RuntimeError, match="must not start a real agent CLI"):
        subprocess.run([str(binary)], check=False)

    assert guard.spawned == [str(binary.resolve())]
    assert not (binary.parent / "ran").exists()
    subprocess.run(["true"], check=True)


async def test_guard_covers_asyncio_subprocesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _fake_cli(tmp_path / "bin")
    guard = AgentCliGuard([])
    guard.install(monkeypatch)

    with pytest.raises(RuntimeError):
        await asyncio.create_subprocess_exec(str(binary))

    assert guard.spawned == [str(binary.resolve())]
    assert not os.path.exists(binary.parent / "ran")


async def test_verified_account_probe_stays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status_probes: list[dict[str, str] | None] = []

    async def fake_probe_codex_status(
        *, cwd: str, binary: str, env: dict[str, str] | None = None
    ) -> None:
        status_probes.append(env)

    monkeypatch.setattr(codex_plugin, "probe_codex_status", fake_probe_codex_status)
    settings = Settings(data_dir=tmp_path / "data")
    runtime = SessionRuntime(settings, Storage(settings.database_path))

    runtime._schedule_verified_account_probe(
        "session-1",
        "codex",
        {"CODEX_HOME": str(tmp_path / "codex-work")},
        launch_target=None,
        cwd=str(tmp_path),
    )
    await runtime._drain_account_probe_tasks()

    assert status_probes == []
