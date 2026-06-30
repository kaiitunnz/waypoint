from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from waypointctl import update


def _fake_run(
    *,
    dirty: str = "",
    tags: str = "v1.0.0\n",
    remote_has=lambda ref: False,
    managed: bool = False,
):
    """A subprocess.run stub that dispatches on the git subcommand."""

    def run(argv, **kwargs):
        if "config" in argv and "waypoint.managed" in argv:
            return MagicMock(returncode=0, stdout="true\n" if managed else "")
        if "status" in argv:
            return MagicMock(returncode=0, stdout=dirty)
        if "tag" in argv and "--list" in argv:
            return MagicMock(returncode=0, stdout=tags)
        if "rev-parse" in argv:
            ref = argv[-1].rsplit("/", 1)[-1]
            return MagicMock(returncode=0 if remote_has(ref) else 1, stdout="")
        return MagicMock(returncode=0, stdout="")

    return run


def _fake_check_run(
    *,
    head: str,
    target: str,
    tags: str = "v1.0.0\n",
    remote_has=lambda ref: False,
):
    """A subprocess.run stub for the read-only `--check` path.

    A `--verify` rev-parse probes existence (returncode); a plain rev-parse
    resolves a SHA (stdout): HEAD -> head, anything else -> target.
    """

    def run(argv, **kwargs):
        if "tag" in argv and "--list" in argv:
            return MagicMock(returncode=0, stdout=tags)
        if "rev-parse" in argv and "--verify" in argv:
            ref = argv[-1].rsplit("/", 1)[-1]
            return MagicMock(returncode=0 if remote_has(ref) else 1, stdout="")
        if "rev-parse" in argv:
            sha = head if argv[-1] == "HEAD" else target
            return MagicMock(returncode=0, stdout=f"{sha}\n")
        return MagicMock(returncode=0, stdout="")

    return run


def _argvs(mock_run) -> list[list[str]]:
    return [c.args[0] for c in mock_run.call_args_list]


def _checkout_cmd(argvs: list[list[str]]) -> list[str]:
    return next(a for a in argvs if "checkout" in a and "--detach" in a)


def test_explicit_ref_checks_out_a_tag(tmp_path: Path) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch("waypointctl.update.subprocess.run", side_effect=_fake_run()) as mock_run,
    ):
        update.run(tmp_path, ref="v1.2.3")

    argvs = _argvs(mock_run)
    assert ["git", "-C", str(tmp_path), "fetch", "--force", "--tags", "origin"] in argvs
    assert not any(
        "--list" in a for a in argvs
    )  # no latest-tag lookup with explicit ref
    assert _checkout_cmd(argvs) == [
        "git",
        "-C",
        str(tmp_path),
        "checkout",
        "--detach",
        "v1.2.3",
    ]


def test_latest_tag_resolves_newest(tmp_path: Path) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.update.subprocess.run",
            side_effect=_fake_run(tags="v3.0.0\nv2.0.0\nv1.0.0\n"),
        ) as mock_run,
    ):
        update.run(tmp_path)

    argvs = _argvs(mock_run)
    tag_cmd = next(a for a in argvs if "--list" in a)
    assert "--sort=-version:refname" in tag_cmd
    assert _checkout_cmd(argvs)[-1] == "v3.0.0"  # newest, not nearest ancestor


def test_nightly_tracks_remote_main(tmp_path: Path) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.update.subprocess.run",
            side_effect=_fake_run(remote_has=lambda ref: ref == "main"),
        ) as mock_run,
    ):
        update.run(tmp_path, nightly=True)

    argvs = _argvs(mock_run)
    assert not any("--list" in a for a in argvs)
    # a branch ref must check out the remote tip, not the stale local ref
    assert _checkout_cmd(argvs) == [
        "git",
        "-C",
        str(tmp_path),
        "checkout",
        "--detach",
        "origin/main",
    ]


def test_branch_ref_tracks_remote_tip(tmp_path: Path) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.update.subprocess.run",
            side_effect=_fake_run(remote_has=lambda ref: ref == "feature"),
        ) as mock_run,
    ):
        update.run(tmp_path, ref="feature")

    assert _checkout_cmd(_argvs(mock_run))[-1] == "origin/feature"


def test_dirty_tree_refuses(tmp_path: Path) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.update.subprocess.run", side_effect=_fake_run(dirty=" M f.py")
        ) as mock_run,
    ):
        with pytest.raises(RuntimeError, match="uncommitted changes"):
            update.run(tmp_path, ref="v1.0.0")

    argvs = _argvs(mock_run)
    assert any("status" in a for a in argvs)
    assert not any("checkout" in a for a in argvs)  # bailed before mutating anything
    assert not any(a and a[0] == "uv" for a in argvs)


def test_nightly_with_ref_is_rejected(tmp_path: Path) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch("waypointctl.update.subprocess.run", side_effect=_fake_run()) as mock_run,
    ):
        with pytest.raises(typer.BadParameter):
            update.run(tmp_path, ref="v1.0.0", nightly=True)

    assert mock_run.call_args_list == []  # rejected before touching git


def test_uv_force_and_no_restart(tmp_path: Path) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch("waypointctl.update.subprocess.run", side_effect=_fake_run()) as mock_run,
    ):
        update.run(tmp_path, ref="v1.0.0")

    argvs = _argvs(mock_run)
    uv_cmd = next(a for a in argvs if a and a[0] == "uv")
    # --reinstall is required so a tag-only version bump isn't masked by uv's cache
    assert "--reinstall" in uv_cmd
    assert "--force" in uv_cmd
    # update reinstalls the tool but leaves the stack alone
    assert not any("restart" in a for a in argvs)
    assert not any(a and a[0] == "waypointctl" for a in argvs)


def test_managed_update_discards_generated_files(tmp_path: Path) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.update.subprocess.run", side_effect=_fake_run(managed=True)
        ) as mock_run,
    ):
        update.run(tmp_path, ref="v1.0.0")

    argvs = _argvs(mock_run)
    for rel in ("frontend/next-env.d.ts", "frontend/tsconfig.json"):
        assert ["git", "-C", str(tmp_path), "checkout", "--", rel] in argvs
    # the discard doesn't abort the update
    assert any(a and a[0] == "uv" and "--force" in a for a in argvs)


def test_unmanaged_update_skips_discard(tmp_path: Path) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.update.subprocess.run", side_effect=_fake_run(managed=False)
        ) as mock_run,
    ):
        update.run(tmp_path, ref="v1.0.0")

    argvs = _argvs(mock_run)
    discards = [
        a
        for a in argvs
        if "checkout" in a and "--" in a and any("frontend/" in x for x in a)
    ]
    assert discards == []


def test_resolve_home_uses_provided_path(tmp_path: Path) -> None:
    with patch(
        "waypointctl.update.resolve_waypoint_home", return_value=tmp_path
    ) as mock_resolve:
        result = update._resolve_home(tmp_path)

    mock_resolve.assert_called_once_with(tmp_path)
    assert result == tmp_path


def test_resolve_home_fallback_when_default_is_a_repo(tmp_path: Path) -> None:
    app = tmp_path / ".waypoint" / "app"
    (app / "backend").mkdir(parents=True)
    (app / "frontend").mkdir(parents=True)
    with (
        patch(
            "waypointctl.update.resolve_waypoint_home",
            side_effect=RuntimeError("no WAYPOINT_HOME"),
        ),
        patch("waypointctl.update.Path.home", return_value=tmp_path),
    ):
        assert update._resolve_home(None) == app


def test_resolve_home_reraises_when_default_missing(tmp_path: Path) -> None:
    with (
        patch(
            "waypointctl.update.resolve_waypoint_home",
            side_effect=RuntimeError("no WAYPOINT_HOME"),
        ),
        patch("waypointctl.update.Path.home", return_value=tmp_path),
        pytest.raises(RuntimeError),
    ):
        update._resolve_home(None)


def test_check_reports_up_to_date(tmp_path: Path, capsys) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.update.subprocess.run",
            side_effect=_fake_check_run(head="abc123", target="abc123"),
        ) as mock_run,
    ):
        update.run(tmp_path, check=True)

    assert "Up to date" in capsys.readouterr().out
    argvs = _argvs(mock_run)
    assert ["git", "-C", str(tmp_path), "fetch", "--force", "--tags", "origin"] in argvs
    # a check never rewrites the working tree or reinstalls the tool
    assert not any("checkout" in a for a in argvs)
    assert not any(a and a[0] == "uv" for a in argvs)


def test_check_reports_update_available(tmp_path: Path, capsys) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.update.subprocess.run",
            side_effect=_fake_check_run(head="abc123", target="def456"),
        ) as mock_run,
    ):
        update.run(tmp_path, check=True)

    out = capsys.readouterr().out
    assert "Update available" in out
    assert "v1.0.0" in out  # the resolved target tag
    # default mode suggests the bare apply command
    assert "Run 'waypointctl update' to apply." in out
    argvs = _argvs(mock_run)
    assert not any("checkout" in a for a in argvs)
    assert not any(a and a[0] == "uv" for a in argvs)


def test_check_apply_command_matches_nightly_mode(tmp_path: Path, capsys) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.update.subprocess.run",
            side_effect=_fake_check_run(head="abc123", target="def456"),
        ),
    ):
        update.run(tmp_path, check=True, nightly=True)

    assert "Run 'waypointctl update --nightly' to apply." in capsys.readouterr().out


def test_check_apply_command_matches_ref_mode(tmp_path: Path, capsys) -> None:
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.update.subprocess.run",
            side_effect=_fake_check_run(head="abc123", target="def456"),
        ),
    ):
        update.run(tmp_path, check=True, ref="v2.0.0")

    assert "Run 'waypointctl update --ref v2.0.0' to apply." in capsys.readouterr().out


def test_check_skips_dirty_guard(tmp_path: Path) -> None:
    # A check is read-only, so a dirty working tree must not block it.
    with (
        patch("waypointctl.update.resolve_waypoint_home", return_value=tmp_path),
        patch(
            "waypointctl.update.subprocess.run",
            side_effect=_fake_check_run(head="abc123", target="abc123"),
        ) as mock_run,
    ):
        update.run(tmp_path, check=True)

    assert not any("status" in a and "--porcelain" in a for a in _argvs(mock_run))
