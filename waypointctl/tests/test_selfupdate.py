from pathlib import Path
from unittest.mock import MagicMock, patch

from waypointctl import selfupdate


def test_explicit_ref_used_directly(tmp_path: Path) -> None:
    with (
        patch("waypointctl.selfupdate.resolve_waypoint_home", return_value=tmp_path),
        patch("waypointctl.selfupdate.subprocess.run") as mock_run,
    ):
        selfupdate.run(tmp_path, ref="v1.2.3")

    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert ["git", "-C", str(tmp_path), "fetch", "--tags"] in cmds
    assert ["git", "-C", str(tmp_path), "checkout", "v1.2.3"] in cmds
    assert not any("describe" in " ".join(cmd) for cmd in cmds)


def test_latest_tag_resolved_when_no_ref(tmp_path: Path) -> None:
    side_effects = [
        MagicMock(),  # git fetch
        MagicMock(stdout="v2.0.0\n"),  # git describe
        MagicMock(),  # git checkout
        MagicMock(),  # uv tool install
        MagicMock(),  # waypointctl restart
    ]
    with (
        patch("waypointctl.selfupdate.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.selfupdate.subprocess.run", side_effect=side_effects
        ) as mock_run,
    ):
        selfupdate.run(tmp_path)

    cmds = [c.args[0] for c in mock_run.call_args_list]
    assert any("describe" in " ".join(cmd) for cmd in cmds)
    assert ["git", "-C", str(tmp_path), "checkout", "v2.0.0"] in cmds


def test_command_sequence_with_explicit_ref(tmp_path: Path) -> None:
    with (
        patch("waypointctl.selfupdate.resolve_waypoint_home", return_value=tmp_path),
        patch("waypointctl.selfupdate.subprocess.run") as mock_run,
    ):
        selfupdate.run(tmp_path, ref="v3.0.0")

    expected = [
        ["git", "-C", str(tmp_path), "fetch", "--tags"],
        ["git", "-C", str(tmp_path), "checkout", "v3.0.0"],
        ["uv", "tool", "install", str(tmp_path / "waypointctl")],
        ["waypointctl", "--home", str(tmp_path), "restart"],
    ]
    assert [c.args[0] for c in mock_run.call_args_list] == expected


def test_resolve_home_fallback_to_default_install_dir() -> None:
    expected = Path.home() / ".waypoint" / "app"
    with patch(
        "waypointctl.selfupdate.resolve_waypoint_home",
        side_effect=RuntimeError("no WAYPOINT_HOME"),
    ):
        result = selfupdate._resolve_home(None)

    assert result == expected


def test_resolve_home_uses_provided_path(tmp_path: Path) -> None:
    with patch(
        "waypointctl.selfupdate.resolve_waypoint_home", return_value=tmp_path
    ) as mock_resolve:
        result = selfupdate._resolve_home(tmp_path)

    mock_resolve.assert_called_once_with(tmp_path)
    assert result == tmp_path


def test_run_fallback_home_used_in_commands() -> None:
    expected_home = Path.home() / ".waypoint" / "app"
    side_effects = [
        MagicMock(),  # git fetch
        MagicMock(stdout="v1.0.0\n"),  # git describe
        MagicMock(),  # git checkout
        MagicMock(),  # uv tool install
        MagicMock(),  # waypointctl restart
    ]
    with (
        patch(
            "waypointctl.selfupdate.resolve_waypoint_home",
            side_effect=RuntimeError("no home"),
        ),
        patch(
            "waypointctl.selfupdate.subprocess.run", side_effect=side_effects
        ) as mock_run,
    ):
        selfupdate.run(None)

    cmds = [c.args[0] for c in mock_run.call_args_list]
    fetch_cmd = next(cmd for cmd in cmds if "fetch" in cmd)
    assert str(expected_home) in fetch_cmd
