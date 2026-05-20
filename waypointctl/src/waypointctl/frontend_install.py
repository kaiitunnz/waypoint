import subprocess
from pathlib import Path


def needs_install(home: Path) -> bool:
    frontend_dir = home / "frontend"
    node_modules = frontend_dir / "node_modules"
    if not node_modules.is_dir():
        return True

    install_marker = node_modules / ".package-lock.json"
    if not install_marker.exists():
        return True

    marker_mtime = install_marker.stat().st_mtime
    for name in ("package-lock.json", "package.json"):
        path = frontend_dir / name
        try:
            if path.stat().st_mtime > marker_mtime:
                return True
        except FileNotFoundError:
            continue
    return False


def run_install(frontend_dir: Path, log_path: Path) -> int:
    command = (
        ["npm", "ci"]
        if (frontend_dir / "package-lock.json").exists()
        else ["npm", "install"]
    )
    with log_path.open("a", encoding="utf-8") as log_file:
        proc = subprocess.run(
            command,
            cwd=frontend_dir,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return proc.returncode
