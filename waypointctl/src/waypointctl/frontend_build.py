import subprocess
from pathlib import Path

BUILD_INPUTS_RELATIVE: tuple[str, ...] = (
    "frontend/src",
    "frontend/public",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/next.config.ts",
    "frontend/tsconfig.json",
)


def is_fresh(home: Path, *, force: bool = False) -> bool:
    if force:
        return False

    next_dir = home / "frontend" / ".next"
    marker = next_dir / "BUILD_ID"
    if not marker.exists():
        return False

    ref_file = next_dir / "BUILD_REF"
    if ref_file.exists():
        current_ref = _current_git_head(home)
        last_ref = _read_ref(ref_file)
        if current_ref and last_ref and current_ref != last_ref:
            changes = _git_diff_inputs(home, last_ref, current_ref)
            if changes is None:
                return False
            if changes:
                return False
            record_build_ref(home)

    return not _has_newer_input(home, marker)


def record_build_ref(home: Path) -> None:
    head = _current_git_head(home)
    if not head:
        return
    ref_file = home / "frontend" / ".next" / "BUILD_REF"
    if not ref_file.parent.exists():
        return
    ref_file.write_text(f"{head}\n", encoding="utf-8")


def _read_ref(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip() or None
    except OSError:
        return None


def _current_git_head(home: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(home), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_diff_inputs(home: Path, base: str, head: str) -> list[str] | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(home),
                "diff",
                "--name-only",
                base,
                head,
                "--",
                *BUILD_INPUTS_RELATIVE,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line]


def _has_newer_input(home: Path, marker: Path) -> bool:
    marker_mtime = marker.stat().st_mtime
    inputs = [home / rel for rel in BUILD_INPUTS_RELATIVE if (home / rel).exists()]
    if not inputs:
        return True
    for path in inputs:
        if _any_newer(path, marker_mtime):
            return True
    return False


def _any_newer(path: Path, marker_mtime: float) -> bool:
    try:
        if path.is_file():
            return path.stat().st_mtime > marker_mtime
        for sub in path.rglob("*"):
            try:
                if sub.is_file() and sub.stat().st_mtime > marker_mtime:
                    return True
            except FileNotFoundError:
                continue
    except FileNotFoundError:
        return False
    return False
