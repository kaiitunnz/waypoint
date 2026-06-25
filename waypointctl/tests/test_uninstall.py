import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from waypointctl import uninstall

REPO_ROOT = Path(__file__).resolve().parents[2]
UNINSTALL_SH = REPO_ROOT / "scripts" / "uninstall.sh"


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / "app"
    (home / "scripts").mkdir(parents=True)
    (home / "scripts" / "uninstall.sh").write_text("#!/usr/bin/env bash\n")
    return home


def test_runs_staged_copy_with_home(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        # the staged script must exist (and live outside the checkout) at call time
        seen["staged_exists"] = Path(argv[1]).is_file()
        seen["outside_home"] = home not in Path(argv[1]).parents

    with patch("waypointctl.uninstall.subprocess.run", side_effect=fake_run):
        uninstall.run(home)

    argv = seen["argv"]
    assert argv[0] == "bash"
    assert Path(argv[1]).name == "uninstall.sh"
    assert argv[2:] == ["--home", str(home)]
    assert seen["staged_exists"] is True
    assert seen["outside_home"] is True


def test_purge_appends_flag(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    with patch("waypointctl.uninstall.subprocess.run") as mock_run:
        uninstall.run(home, purge=True)
    argv = mock_run.call_args.args[0]
    assert argv[-1] == "--purge"
    assert mock_run.call_args.kwargs.get("check") is True


def test_force_appends_flag(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    with patch("waypointctl.uninstall.subprocess.run") as mock_run:
        uninstall.run(home, force=True)
    assert "--force" in mock_run.call_args.args[0]


def test_missing_script_raises(tmp_path: Path) -> None:
    home = tmp_path / "app"
    home.mkdir()
    with (
        patch("waypointctl.uninstall.subprocess.run") as mock_run,
        pytest.raises(RuntimeError, match="uninstall script not found"),
    ):
        uninstall.run(home)
    mock_run.assert_not_called()


# ── integration tests that drive the real scripts/uninstall.sh ──────────────

WP_BLOCK = '# >>> waypoint >>>\nexport WAYPOINT_HOME="x"\n# <<< waypoint <<<'


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _sandbox(
    tmp_path: Path, *, managed: bool = True, data_env: str | None = None
) -> tuple[Path, Path, Path]:
    """Build a fake HOME with a managed checkout, data dir, rc file, and stubs."""
    home = tmp_path / "home"
    app = home / ".waypoint" / "app"
    (app / "backend").mkdir(parents=True)
    (app / "frontend").mkdir()
    _git(["init", "-q"], app)
    _git(["config", "user.email", "t@t"], app)
    _git(["config", "user.name", "t"], app)
    if managed:
        _git(["config", "waypoint.managed", "true"], app)

    data = home / ".waypoint" / "backend-data"
    data.mkdir(parents=True)
    (data / "db").write_text("data")
    if data_env is not None:
        (app / ".env").write_text(f"WAYPOINT_STACK_BACKEND_DATA_DIR={data_env}\n")

    (home / ".bashrc").write_text(f"before\n\n{WP_BLOCK}\nafter\n")

    # Run the script from outside the checkout (as the wrapper / curl pipe does),
    # so deleting the checkout can't pull the running script.
    staged = tmp_path / "uninstall.sh"
    shutil.copy2(UNINSTALL_SH, staged)
    stub = tmp_path / "stub"
    stub.mkdir()
    for name in ("waypointctl", "uv"):
        p = stub / name
        p.write_text("#!/usr/bin/env bash\nexit 0\n")
        p.chmod(0o755)
    return home, app, stub


def _run(tmp_path: Path, home: Path, app: Path, stub: Path, *args: str) -> None:
    env = {"HOME": str(home), "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}"}
    subprocess.run(
        ["bash", str(tmp_path / "uninstall.sh"), "--home", str(app), *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_script_default_removes_checkout_keeps_data(tmp_path: Path) -> None:
    home, app, stub = _sandbox(tmp_path)
    _run(tmp_path, home, app, stub)
    assert not app.exists()
    assert (home / ".waypoint" / "backend-data" / "db").exists()
    # the block (and the blank line install.sh prepends) is gone, rest preserved
    assert (home / ".bashrc").read_text() == "before\nafter\n"


def test_script_purge_removes_state_and_data(tmp_path: Path) -> None:
    home, app, stub = _sandbox(tmp_path)
    _run(tmp_path, home, app, stub, "--purge")
    assert not (home / ".waypoint").exists()


def test_script_tilde_data_dir_resolves_outside_checkout(tmp_path: Path) -> None:
    # ~ must expand like config.py; the default checkout removal must still run
    home, app, stub = _sandbox(tmp_path, data_env="~/.waypoint/backend-data")
    _run(tmp_path, home, app, stub)
    assert not app.exists()
    assert (home / ".waypoint" / "backend-data" / "db").exists()


def test_script_relative_data_inside_checkout_is_preserved(tmp_path: Path) -> None:
    home, app, stub = _sandbox(tmp_path, data_env="./data")
    (app / "data").mkdir()
    _run(tmp_path, home, app, stub)
    assert app.exists()  # kept so the inside-checkout data survives


def test_script_non_managed_checkout_preserved_without_force(tmp_path: Path) -> None:
    home, app, stub = _sandbox(tmp_path, managed=False)
    _run(tmp_path, home, app, stub)
    assert app.exists()
    _run(tmp_path, home, app, stub, "--force")
    assert not app.exists()
