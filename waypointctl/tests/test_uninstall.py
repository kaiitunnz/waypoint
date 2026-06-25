from pathlib import Path
from unittest.mock import patch

import pytest

from waypointctl import uninstall


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


def test_missing_script_raises(tmp_path: Path) -> None:
    home = tmp_path / "app"
    home.mkdir()
    with (
        patch("waypointctl.uninstall.subprocess.run") as mock_run,
        pytest.raises(RuntimeError, match="uninstall script not found"),
    ):
        uninstall.run(home)
    mock_run.assert_not_called()
