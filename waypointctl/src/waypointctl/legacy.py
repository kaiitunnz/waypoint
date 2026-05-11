from __future__ import annotations

import os
import subprocess
from pathlib import Path

from waypointctl.paths import waypoint_script_path


def run_legacy_command(
    home: Path, command: str, args: list[str]
) -> subprocess.CompletedProcess[str]:
    script = waypoint_script_path(home)
    if not script.exists():
        raise FileNotFoundError(f"legacy supervisor script not found: {script}")

    env = os.environ.copy()
    env.setdefault("WAYPOINT_HOME", str(home))
    return subprocess.run(
        ["bash", str(script), command, *args],
        cwd=home,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
