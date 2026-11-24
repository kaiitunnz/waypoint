import shutil
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path

from waypointctl.workspace import APP_DIRS, ROOT_LOCKFILE, WORKSPACE_MANIFESTS

# Frontend services start in parallel but share one root node_modules.
install_lock = threading.Lock()


def needs_install(home: Path) -> bool:
    node_modules = home / "node_modules"
    if not node_modules.is_dir():
        return True

    install_marker = node_modules / ".package-lock.json"
    if not install_marker.exists():
        return True

    if _legacy_node_modules(home):
        return True

    marker_mtime = install_marker.stat().st_mtime
    for name in (ROOT_LOCKFILE, *WORKSPACE_MANIFESTS):
        path = home / name
        try:
            if path.stat().st_mtime > marker_mtime:
                return True
        except FileNotFoundError:
            continue
    return False


def run_install(home: Path, log_path: Path, env: Mapping[str, str]) -> int:
    for legacy in _legacy_node_modules(home):
        shutil.rmtree(legacy, ignore_errors=True)

    command = ["npm", "ci"] if (home / ROOT_LOCKFILE).exists() else ["npm", "install"]
    with log_path.open("a", encoding="utf-8") as log_file:
        proc = subprocess.run(
            command,
            cwd=home,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return proc.returncode


def _legacy_node_modules(home: Path) -> list[Path]:
    # npm writes `.package-lock.json` only at an install root, so one inside an
    # app directory is a standalone install that would shadow the shared one.
    return [
        path
        for app_dir in APP_DIRS.values()
        if (path := home / app_dir / "node_modules").is_dir()
        and (path / ".package-lock.json").exists()
    ]
