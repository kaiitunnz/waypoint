import os
import subprocess
import time
from pathlib import Path

import pytest

from waypointctl.frontend_build import (
    build_inputs,
    is_fresh,
    record_build,
    record_build_ref,
)
from waypointctl.workspace import APP_DIRS, FrontendVariant

DEFAULT_BACKEND_PORT = 8787

VARIANTS: tuple[FrontendVariant, ...] = ("v1", "v2")

_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _make_repo(root: Path) -> None:
    for app_dir in APP_DIRS.values():
        (root / app_dir / "src").mkdir(parents=True)
        (root / app_dir / "public").mkdir()
        (root / app_dir / "package.json").write_text("{}")
        (root / app_dir / "next.config.ts").write_text("export default {}")
        (root / app_dir / "tsconfig.json").write_text("{}")
        (root / app_dir / "src" / "index.ts").write_text("export {}")
    (root / "packages" / "web-core" / "src").mkdir(parents=True)
    (root / "packages" / "web-core" / "package.json").write_text("{}")
    (root / "packages" / "web-core" / "src" / "index.ts").write_text("export {}")
    (root / "package.json").write_text("{}")
    (root / "package-lock.json").write_text("{}")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, env={**os.environ, **_GIT_ENV})


def _init_git(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")


def _commit_all(root: Path, message: str) -> None:
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", message)


def _age_inputs(root: Path, seconds: float = 60) -> None:
    past = time.time() - seconds
    for path in [root / "package.json", root / "package-lock.json"]:
        os.utime(path, (past, past))
    for rel in (*APP_DIRS.values(), "packages/web-core"):
        for path in (root / rel).rglob("*"):
            if path.is_file():
                os.utime(path, (past, past))


def _next_dir(root: Path, variant: FrontendVariant) -> Path:
    return root / APP_DIRS[variant] / ".next"


def _write_build_id(
    root: Path, variant: FrontendVariant, mtime: float | None = None
) -> Path:
    next_dir = _next_dir(root, variant)
    next_dir.mkdir(parents=True, exist_ok=True)
    marker = next_dir / "BUILD_ID"
    marker.write_text("build")
    if mtime is not None:
        os.utime(marker, (mtime, mtime))
    return marker


def _write_port_marker(
    root: Path, variant: FrontendVariant, port: int = DEFAULT_BACKEND_PORT
) -> None:
    next_dir = _next_dir(root, variant)
    next_dir.mkdir(parents=True, exist_ok=True)
    (next_dir / "BUILD_BACKEND_PORT").write_text(f"{port}\n")


def _built(root: Path, variant: FrontendVariant) -> None:
    # Newer than inputs aged by _age_inputs, older than any edit made after.
    _write_build_id(root, variant, mtime=time.time() - 30)
    _write_port_marker(root, variant)


def _current_head(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _fresh(root: Path, variant: FrontendVariant) -> bool:
    return is_fresh(root, variant, backend_port=DEFAULT_BACKEND_PORT)


def test_build_inputs_are_per_app_plus_shared_workspace_inputs() -> None:
    v1 = build_inputs("v1")
    v2 = build_inputs("v2")

    assert "frontend/src" in v1 and "frontend-v2/src" not in v1
    assert "frontend-v2/src" in v2 and "frontend/src" not in v2
    for shared in ("package-lock.json", "package.json", "packages/web-core/src"):
        assert shared in v1 and shared in v2
    assert "frontend/package-lock.json" not in v1


def test_is_fresh_force_returns_not_fresh(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _built(tmp_path, "v1")
    assert (
        is_fresh(tmp_path, "v1", backend_port=DEFAULT_BACKEND_PORT, force=True) is False
    )


def test_is_fresh_no_build_id(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    assert _fresh(tmp_path, "v1") is False


@pytest.mark.parametrize("variant", VARIANTS)
def test_is_fresh_mtime_only_when_inputs_older(
    tmp_path: Path, variant: FrontendVariant
) -> None:
    _make_repo(tmp_path)
    _age_inputs(tmp_path)
    _built(tmp_path, variant)
    assert _fresh(tmp_path, variant) is True


def test_is_fresh_mtime_only_when_inputs_newer(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _write_build_id(tmp_path, "v1", mtime=time.time() - 60)
    _write_port_marker(tmp_path, "v1")
    assert _fresh(tmp_path, "v1") is False


@pytest.mark.parametrize(
    ("variant", "touched"),
    [
        ("v1", "package-lock.json"),
        ("v2", "package.json"),
        ("v1", "packages/web-core/src/index.ts"),
        ("v2", "packages/web-core/package.json"),
    ],
)
def test_is_fresh_shared_input_edit_invalidates_the_app(
    tmp_path: Path, variant: FrontendVariant, touched: str
) -> None:
    _make_repo(tmp_path)
    _age_inputs(tmp_path)
    _built(tmp_path, variant)
    assert _fresh(tmp_path, variant) is True

    (tmp_path / touched).write_text("changed")

    assert _fresh(tmp_path, variant) is False


@pytest.mark.parametrize("variant", VARIANTS)
def test_is_fresh_ignores_edits_to_the_other_app(
    tmp_path: Path, variant: FrontendVariant
) -> None:
    other = "v2" if variant == "v1" else "v1"
    _make_repo(tmp_path)
    _age_inputs(tmp_path)
    _built(tmp_path, variant)

    (tmp_path / APP_DIRS[other] / "src" / "index.ts").write_text("export const X = 1")

    assert _fresh(tmp_path, variant) is True


def test_is_fresh_ref_match_falls_through_to_mtime(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _init_git(tmp_path)
    _age_inputs(tmp_path)
    _built(tmp_path, "v1")
    (_next_dir(tmp_path, "v1") / "BUILD_REF").write_text(_current_head(tmp_path) + "\n")
    assert _fresh(tmp_path, "v1") is True


@pytest.mark.parametrize(
    ("variant", "changed"),
    [("v1", "package-lock.json"), ("v2", "packages/web-core/src/index.ts")],
)
def test_is_fresh_ref_diverged_with_shared_input_change(
    tmp_path: Path, variant: FrontendVariant, changed: str
) -> None:
    _make_repo(tmp_path)
    _init_git(tmp_path)
    initial_head = _current_head(tmp_path)
    _age_inputs(tmp_path)
    _built(tmp_path, variant)
    (_next_dir(tmp_path, variant) / "BUILD_REF").write_text(initial_head + "\n")

    (tmp_path / changed).write_text("changed")
    _commit_all(tmp_path, "change")
    _age_inputs(tmp_path)

    assert _fresh(tmp_path, variant) is False


def test_is_fresh_ref_diverged_other_app_change_keeps_build(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _init_git(tmp_path)
    initial_head = _current_head(tmp_path)
    _age_inputs(tmp_path)
    _built(tmp_path, "v1")
    (_next_dir(tmp_path, "v1") / "BUILD_REF").write_text(initial_head + "\n")

    (tmp_path / "frontend-v2" / "src" / "index.ts").write_text("export const X = 1")
    _commit_all(tmp_path, "v2 only")
    _age_inputs(tmp_path)

    assert _fresh(tmp_path, "v1") is True
    assert (
        _next_dir(tmp_path, "v1") / "BUILD_REF"
    ).read_text().strip() == _current_head(tmp_path)


def test_is_fresh_ref_diverged_unrelated_change_advances_ref(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    (tmp_path / "README.md").write_text("hi")
    _init_git(tmp_path)
    initial_head = _current_head(tmp_path)
    _age_inputs(tmp_path)
    _built(tmp_path, "v2")
    (_next_dir(tmp_path, "v2") / "BUILD_REF").write_text(initial_head + "\n")

    (tmp_path / "README.md").write_text("hi again")
    _commit_all(tmp_path, "docs")

    assert _fresh(tmp_path, "v2") is True
    assert (
        _next_dir(tmp_path, "v2") / "BUILD_REF"
    ).read_text().strip() == _current_head(tmp_path)


def test_is_fresh_returns_false_when_port_marker_missing(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _age_inputs(tmp_path)
    _write_build_id(tmp_path, "v1")
    # no BUILD_BACKEND_PORT marker — legacy build, must rebuild
    assert _fresh(tmp_path, "v1") is False


def test_is_fresh_returns_false_when_backend_port_changes(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _age_inputs(tmp_path)
    _write_build_id(tmp_path, "v1")
    _write_port_marker(tmp_path, "v1", port=8787)
    assert is_fresh(tmp_path, "v1", backend_port=8797) is False


def test_markers_are_per_app(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _age_inputs(tmp_path)
    _built(tmp_path, "v1")

    assert _fresh(tmp_path, "v1") is True
    assert _fresh(tmp_path, "v2") is False


@pytest.mark.parametrize("variant", VARIANTS)
def test_record_build_writes_port_marker_and_ref(
    tmp_path: Path, variant: FrontendVariant
) -> None:
    _make_repo(tmp_path)
    _init_git(tmp_path)
    _next_dir(tmp_path, variant).mkdir()

    record_build(tmp_path, variant, backend_port=8797)

    next_dir = _next_dir(tmp_path, variant)
    assert (next_dir / "BUILD_BACKEND_PORT").read_text().strip() == "8797"
    assert (next_dir / "BUILD_REF").read_text().strip() == _current_head(tmp_path)


def test_record_build_ref_no_op_without_next_dir(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    _init_git(tmp_path)
    record_build_ref(tmp_path, "v2")
    assert not (_next_dir(tmp_path, "v2") / "BUILD_REF").exists()
