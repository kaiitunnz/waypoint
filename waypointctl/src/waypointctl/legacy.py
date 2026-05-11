import os
import subprocess
from pathlib import Path

from waypointctl.paths import waypoint_script_path


def run_legacy_command(
    home: Path, command: str, args: list[str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _legacy_argv(home, command, args),
        cwd=home,
        env=_legacy_env(home),
        text=True,
        capture_output=True,
        check=False,
    )


def stream_legacy_command(home: Path, command: str, args: list[str]) -> int:
    completed = subprocess.run(
        _legacy_argv(home, command, args),
        cwd=home,
        env=_legacy_env(home),
        check=False,
    )
    return completed.returncode


def _legacy_argv(home: Path, command: str, args: list[str]) -> list[str]:
    script = waypoint_script_path(home)
    if not script.exists():
        raise FileNotFoundError(f"legacy supervisor script not found: {script}")
    return ["bash", str(script), command, *args]


def _legacy_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("WAYPOINT_HOME", str(home))
    return env
