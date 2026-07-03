import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import waypointctl.cli as cli_module
from waypointctl.cli import app


def _make_repo(
    home: Path,
    *,
    skills: tuple[str, ...] = (
        "waypoint-subagents",
        "waypoint-comms",
        "waypoint-workqueue",
        "waypoint-crew",
        "waypoint-worktree",
    ),
) -> Path:
    (home / "backend").mkdir(parents=True)
    (home / "frontend").mkdir()
    (home / "scripts").mkdir()
    for skill in skills:
        skill_dir = home / ".agents" / "skills" / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: test\n---\n", encoding="utf-8"
        )
    return home


def _copy_skills_script(repo: Path) -> Path:
    source = Path(__file__).resolve().parents[2] / "scripts" / "install_skills.sh"
    target = repo / "scripts" / "install_skills.sh"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IEXEC)
    return target


def _run_script(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash") or "/bin/bash"
    return subprocess.run(
        [bash, str(repo / "scripts" / "install_skills.sh"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("command", ["install", "uninstall", "status"])
def test_skills_commands_route_to_helper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
) -> None:
    home = _make_repo(tmp_path / "repo")

    captured: dict[str, object] = {}

    def fake_run_skills_helper(
        resolved_home: Path, helper_command: str, extra: list[str]
    ) -> None:
        captured["home"] = resolved_home
        captured["command"] = helper_command
        captured["extra"] = extra

    monkeypatch.setattr(cli_module, "run_skills_helper", fake_run_skills_helper)

    result = CliRunner().invoke(
        app,
        [
            "--home",
            str(home),
            "skills",
            command,
            "--skill-dir",
            "/tmp/a",
            "--skill",
            "waypoint-subagents",
        ],
    )

    assert result.exit_code == 0
    assert captured["home"] == home
    assert captured["command"] == command
    assert captured["extra"] == [
        "--skill-dir",
        "/tmp/a",
        "--skill",
        "waypoint-subagents",
    ]


def test_skills_install_forwards_all_and_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = _make_repo(tmp_path / "repo")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        cli_module,
        "run_skills_helper",
        lambda h, c, extra: captured.update(extra=extra),
    )

    result = CliRunner().invoke(
        app, ["--home", str(home), "skills", "install", "--all", "--copy"]
    )

    assert result.exit_code == 0
    assert captured["extra"] == ["--all", "--copy"]


def test_helper_install_status_uninstall_roundtrip(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _copy_skills_script(repo)
    dest = tmp_path / "dest"

    installed = _run_script(repo, "install", "--skill-dir", str(dest))
    assert installed.returncode == 0
    link = dest / "waypoint-subagents"
    assert link.is_symlink()
    assert link.resolve() == (repo / ".agents" / "skills" / "waypoint-subagents")
    assert (link / "SKILL.md").is_file()

    status = _run_script(repo, "status", "--skill-dir", str(dest))
    assert status.returncode == 0
    assert "linked" in status.stdout

    removed = _run_script(repo, "uninstall", "--skill-dir", str(dest))
    assert removed.returncode == 0
    assert not link.exists()


def test_helper_default_installs_all_skills(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _copy_skills_script(repo)
    dest = tmp_path / "dest"

    result = _run_script(repo, "install", "--skill-dir", str(dest))

    assert result.returncode == 0
    for skill in (
        "waypoint-subagents",
        "waypoint-comms",
        "waypoint-workqueue",
        "waypoint-crew",
        "waypoint-worktree",
    ):
        assert (dest / skill).is_symlink()


def test_helper_install_is_idempotent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _copy_skills_script(repo)
    dest = tmp_path / "dest"

    first = _run_script(repo, "install", "--skill-dir", str(dest))
    second = _run_script(repo, "install", "--skill-dir", str(dest))

    assert first.returncode == 0
    assert second.returncode == 0
    assert (dest / "waypoint-subagents").is_symlink()


def test_helper_refuses_to_overwrite_foreign_directory(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _copy_skills_script(repo)
    dest = tmp_path / "dest"
    (dest / "waypoint-subagents").mkdir(parents=True)

    result = _run_script(repo, "install", "--skill-dir", str(dest))

    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr
    assert (dest / "waypoint-subagents").is_dir()


def test_helper_uninstall_skips_unmanaged_entry(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _copy_skills_script(repo)
    dest = tmp_path / "dest"
    foreign = dest / "waypoint-subagents"
    foreign.mkdir(parents=True)

    result = _run_script(repo, "uninstall", "--skill-dir", str(dest))

    assert result.returncode == 0
    assert "not a managed symlink" in result.stderr
    assert foreign.is_dir()


def test_helper_copy_mode_detaches_from_repo(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _copy_skills_script(repo)
    dest = tmp_path / "dest"

    result = _run_script(repo, "install", "--copy", "--skill-dir", str(dest))

    assert result.returncode == 0
    entry = dest / "waypoint-subagents"
    assert entry.is_dir() and not entry.is_symlink()
    assert (entry / "SKILL.md").is_file()

    status = _run_script(repo, "status", "--skill-dir", str(dest))
    assert "copied" in status.stdout


def test_helper_all_selects_every_skill(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo", skills=("waypoint-subagents", "other"))
    _copy_skills_script(repo)
    dest = tmp_path / "dest"

    result = _run_script(repo, "install", "--all", "--skill-dir", str(dest))

    assert result.returncode == 0
    assert (dest / "waypoint-subagents").is_symlink()
    assert (dest / "other").is_symlink()


def test_helper_rejects_unknown_skill(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _copy_skills_script(repo)
    dest = tmp_path / "dest"

    result = _run_script(repo, "install", "--skill", "nope", "--skill-dir", str(dest))

    assert result.returncode != 0
    assert "unknown skill: nope" in result.stderr
