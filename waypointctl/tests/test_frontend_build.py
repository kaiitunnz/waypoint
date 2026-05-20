import os
import subprocess
import time
from pathlib import Path

from waypointctl.frontend_build import is_fresh, record_build, record_build_ref


def _make_repo(root: Path) -> None:
    (root / "frontend" / "src").mkdir(parents=True)
    (root / "frontend" / "public").mkdir()
    (root / "frontend" / "package.json").write_text("{}")
    (root / "frontend" / "package-lock.json").write_text("{}")
    (root / "frontend" / "next.config.ts").write_text("export default {}")
    (root / "frontend" / "tsconfig.json").write_text("{}")
    (root / "frontend" / "src" / "index.ts").write_text("export {}")


def _init_git(root: Path) -> None:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, env=env)
    subprocess.run(["git", "add", "."], cwd=root, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True, env=env)


DEFAULT_BACKEND_PORT = 8787


def _write_build_id(root: Path, mtime: float | None = None) -> Path:
    next_dir = root / "frontend" / ".next"
    next_dir.mkdir(parents=True, exist_ok=True)
    marker = next_dir / "BUILD_ID"
    marker.write_text("build")
    if mtime is not None:
        os.utime(marker, (mtime, mtime))
    return marker


def _write_port_marker(root: Path, port: int = DEFAULT_BACKEND_PORT) -> None:
    next_dir = root / "frontend" / ".next"
    next_dir.mkdir(parents=True, exist_ok=True)
    (next_dir / "BUILD_BACKEND_PORT").write_text(f"{port}\n")


def _current_head(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_is_fresh_force_returns_not_fresh(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _write_build_id(tmp_path)
    _write_port_marker(tmp_path)
    assert is_fresh(tmp_path, backend_port=DEFAULT_BACKEND_PORT, force=True) is False


def test_is_fresh_no_build_id(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    assert is_fresh(tmp_path, backend_port=DEFAULT_BACKEND_PORT) is False


def test_is_fresh_mtime_only_when_inputs_older(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    past = time.time() - 60
    for rel in ("frontend/package.json", "frontend/src/index.ts"):
        path = tmp_path / rel
        os.utime(path, (past, past))
    _write_build_id(tmp_path)
    _write_port_marker(tmp_path)
    assert is_fresh(tmp_path, backend_port=DEFAULT_BACKEND_PORT) is True


def test_is_fresh_mtime_only_when_inputs_newer(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _write_build_id(tmp_path, mtime=time.time() - 60)
    _write_port_marker(tmp_path)
    assert is_fresh(tmp_path, backend_port=DEFAULT_BACKEND_PORT) is False


def test_is_fresh_ref_match_falls_through_to_mtime(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _init_git(tmp_path)
    head = _current_head(tmp_path)
    past = time.time() - 60
    for rel in ("frontend/package.json", "frontend/src/index.ts"):
        os.utime(tmp_path / rel, (past, past))
    _write_build_id(tmp_path)
    _write_port_marker(tmp_path)
    (tmp_path / "frontend" / ".next" / "BUILD_REF").write_text(head + "\n")
    assert is_fresh(tmp_path, backend_port=DEFAULT_BACKEND_PORT) is True


def test_is_fresh_ref_diverged_with_input_change(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _init_git(tmp_path)
    initial_head = _current_head(tmp_path)

    _write_build_id(tmp_path)
    _write_port_marker(tmp_path)
    (tmp_path / "frontend" / ".next" / "BUILD_REF").write_text(initial_head + "\n")

    (tmp_path / "frontend" / "src" / "index.ts").write_text("export const X = 1")
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "change"], cwd=tmp_path, check=True, env=env
    )

    assert is_fresh(tmp_path, backend_port=DEFAULT_BACKEND_PORT) is False


def test_is_fresh_ref_diverged_unrelated_change_advances_ref(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    (tmp_path / "README.md").write_text("hi")
    _init_git(tmp_path)
    initial_head = _current_head(tmp_path)

    past = time.time() - 60
    for rel in ("frontend/package.json", "frontend/src/index.ts"):
        os.utime(tmp_path / rel, (past, past))
    _write_build_id(tmp_path)
    _write_port_marker(tmp_path)
    (tmp_path / "frontend" / ".next" / "BUILD_REF").write_text(initial_head + "\n")

    (tmp_path / "README.md").write_text("hi again")
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "docs"], cwd=tmp_path, check=True, env=env
    )

    assert is_fresh(tmp_path, backend_port=DEFAULT_BACKEND_PORT) is True

    new_head = _current_head(tmp_path)
    assert (
        tmp_path / "frontend" / ".next" / "BUILD_REF"
    ).read_text().strip() == new_head


def test_is_fresh_returns_false_when_port_marker_missing(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    past = time.time() - 60
    for rel in ("frontend/package.json", "frontend/src/index.ts"):
        os.utime(tmp_path / rel, (past, past))
    _write_build_id(tmp_path)
    # no BUILD_BACKEND_PORT marker — legacy build, must rebuild
    assert is_fresh(tmp_path, backend_port=DEFAULT_BACKEND_PORT) is False


def test_is_fresh_returns_false_when_backend_port_changes(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    past = time.time() - 60
    for rel in ("frontend/package.json", "frontend/src/index.ts"):
        os.utime(tmp_path / rel, (past, past))
    _write_build_id(tmp_path)
    _write_port_marker(tmp_path, port=8787)
    assert is_fresh(tmp_path, backend_port=8797) is False


def test_record_build_writes_port_marker(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _init_git(tmp_path)
    (tmp_path / "frontend" / ".next").mkdir()
    record_build(tmp_path, backend_port=8797)
    port_marker = tmp_path / "frontend" / ".next" / "BUILD_BACKEND_PORT"
    assert port_marker.read_text().strip() == "8797"


def test_record_build_ref_writes_head(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _init_git(tmp_path)
    (tmp_path / "frontend" / ".next").mkdir()
    record_build_ref(tmp_path)
    assert (
        tmp_path / "frontend" / ".next" / "BUILD_REF"
    ).read_text().strip() == _current_head(tmp_path)


def test_record_build_ref_no_op_without_next_dir(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _init_git(tmp_path)
    record_build_ref(tmp_path)
    assert not (tmp_path / "frontend" / ".next" / "BUILD_REF").exists()
