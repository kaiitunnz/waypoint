import shutil
import subprocess
import tempfile
from pathlib import Path


def run(home: Path, purge: bool = False) -> None:
    """Uninstall Waypoint by running scripts/uninstall.sh from a temp copy.

    The script deletes the checkout it lives in, so it is staged outside the
    checkout first; otherwise removing the directory would yank the script out
    from under the running shell.
    """
    script = home / "scripts" / "uninstall.sh"
    if not script.is_file():
        raise RuntimeError(f"uninstall script not found at {script}")

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / "uninstall.sh"
        shutil.copy2(script, staged)
        argv = ["bash", str(staged), "--home", str(home)]
        if purge:
            argv.append("--purge")
        subprocess.run(argv, check=True)
