import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import waypointctl.cli as cli_module
from waypointctl import tailscale as tailscale_module
from waypointctl.cli import app
from waypointctl.tailscale import ToolAvailability


def _make_repo(home: Path) -> Path:
    (home / "backend").mkdir(parents=True)
    (home / "frontend").mkdir()
    (home / "scripts").mkdir()
    return home


def _copy_tailscale_script(repo: Path) -> Path:
    source = Path(__file__).resolve().parents[2] / "scripts" / "waypoint_tailscale.sh"
    target = repo / "scripts" / "waypoint_tailscale.sh"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IEXEC)
    return target


def _write_fake_docker(bin_dir: Path, log_file: Path) -> Path:
    docker = bin_dir / "docker"
    log_path = shlex.quote(str(log_file))
    docker.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> {log_path}
case "$1" in
  inspect)
    exit 1
    ;;
  run)
    printf 'fake-container-id\\n'
    ;;
  start)
    exit 0
    ;;
  exec)
    if [[ "${{3:-}}" == "tailscale" && "${{4:-}}" == "status" ]]; then
      exit 0
    fi
    if [[ "${{3:-}}" == "tailscale" && "${{4:-}}" == "serve" ]]; then
      exit 0
    fi
    exit 0
    ;;
  stop)
    exit 0
    ;;
  logs)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    return docker


def test_preflight_prompts_when_docker_and_tailscale_are_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        tailscale_module,
        "detect_tool_availability",
        lambda: ToolAvailability(True, True),
    )

    class _FakeStdin:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(tailscale_module.sys, "stdin", _FakeStdin())

    def fake_confirm(message: str, *, default: bool = False) -> bool:
        prompts.append((message, default))
        return True

    monkeypatch.setattr(tailscale_module.typer, "confirm", fake_confirm)

    tailscale_module.preflight_tailscale_command("up")

    assert prompts == [
        (
            "Docker and Tailscale are both installed. Proceed with Docker deployment?",
            False,
        )
    ]


def test_preflight_aborts_when_user_declines_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tailscale_module,
        "detect_tool_availability",
        lambda: ToolAvailability(True, True),
    )

    class _FakeStdin:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(tailscale_module.sys, "stdin", _FakeStdin())
    monkeypatch.setattr(
        tailscale_module.typer,
        "confirm",
        lambda _message, *, default=False: False,
    )

    with pytest.raises(tailscale_module.typer.Exit) as excinfo:
        tailscale_module.preflight_tailscale_command("up")

    assert excinfo.value.exit_code == 1


def test_preflight_aborts_when_tailscale_exists_but_docker_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tailscale_module,
        "detect_tool_availability",
        lambda: ToolAvailability(docker=False, tailscale=True),
    )

    with pytest.raises(tailscale_module.typer.Exit) as excinfo:
        tailscale_module.preflight_tailscale_command("up")

    assert excinfo.value.exit_code == 1


def test_preflight_aborts_when_neither_tool_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tailscale_module,
        "detect_tool_availability",
        lambda: ToolAvailability(docker=False, tailscale=False),
    )

    with pytest.raises(tailscale_module.typer.Exit) as excinfo:
        tailscale_module.preflight_tailscale_command("up")

    assert excinfo.value.exit_code == 1


def test_preflight_status_succeeds_when_docker_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        tailscale_module,
        "detect_tool_availability",
        lambda: ToolAvailability(docker=False, tailscale=False),
    )

    with pytest.raises(tailscale_module.typer.Exit) as excinfo:
        tailscale_module.preflight_tailscale_command("status")

    assert excinfo.value.exit_code == 0
    captured = capsys.readouterr()
    assert "docker not installed" in captured.out


def test_preflight_logs_aborts_when_docker_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        tailscale_module,
        "detect_tool_availability",
        lambda: ToolAvailability(docker=False, tailscale=True),
    )

    with pytest.raises(tailscale_module.typer.Exit) as excinfo:
        tailscale_module.preflight_tailscale_command("logs")

    assert excinfo.value.exit_code == 1
    captured = capsys.readouterr()
    assert "Docker is required to read container logs." in captured.err


def test_tailscale_up_uses_repo_dotenv_and_routes_to_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = _make_repo(tmp_path / "repo")
    (home / ".env").write_text(
        "TS_AUTHKEY=from-dotenv\n"
        "TS_HOSTNAME=waypoint-profile-dotenv\n"
        "TS_IMAGE=tailscale/tailscale:stable\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TS_AUTHKEY", "from-shell")
    monkeypatch.setenv("TS_HOSTNAME", "shell-host")
    monkeypatch.setenv("TS_IMAGE", "shell-image")

    captured: dict[str, object] = {}

    def fake_run_tailscale_helper(
        resolved_home: Path, command: str, profile: str
    ) -> None:
        captured["home"] = resolved_home
        captured["command"] = command
        captured["profile"] = profile
        captured["authkey"] = os.environ["TS_AUTHKEY"]
        captured["hostname"] = os.environ["TS_HOSTNAME"]
        captured["image"] = os.environ["TS_IMAGE"]

    monkeypatch.setattr(
        cli_module, "preflight_tailscale_command", lambda _command: None
    )
    monkeypatch.setattr(cli_module, "run_tailscale_helper", fake_run_tailscale_helper)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--home", str(home), "tailscale", "up", "profile-a"],
    )

    assert result.exit_code == 0
    assert captured["home"] == home
    assert captured["command"] == "up"
    assert captured["profile"] == "profile-a"
    assert captured["authkey"] == "from-dotenv"
    assert captured["hostname"] == "waypoint-profile-dotenv"
    assert captured["image"] == "tailscale/tailscale:stable"


@pytest.mark.parametrize("command", ["down", "status", "logs"])
def test_tailscale_verbs_route_to_helper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    command: str,
) -> None:
    home = _make_repo(tmp_path / "repo")

    captured: dict[str, object] = {}

    def fake_run_tailscale_helper(
        resolved_home: Path, helper_command: str, profile: str
    ) -> None:
        captured["home"] = resolved_home
        captured["command"] = helper_command
        captured["profile"] = profile

    monkeypatch.setattr(
        cli_module, "preflight_tailscale_command", lambda _command: None
    )
    monkeypatch.setattr(cli_module, "run_tailscale_helper", fake_run_tailscale_helper)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["--home", str(home), "tailscale", command, "profile-a"],
    )

    assert result.exit_code == 0
    assert captured["home"] == home
    assert captured["command"] == command
    assert captured["profile"] == "profile-a"


def test_helper_up_uses_repo_dotenv_and_exposes_ports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _make_repo(tmp_path / "repo")
    _copy_tailscale_script(repo)
    (repo / ".env").write_text(
        "TS_AUTHKEY=tskey-auth-123\n"
        "TS_HOSTNAME=wp-profile-a\n"
        "TS_IMAGE=tailscale/tailscale:stable\n",
        encoding="utf-8",
    )
    log_file = tmp_path / "docker.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_docker(bin_dir, log_file)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("TS_AUTHKEY", "from-shell")
    monkeypatch.setenv("TS_HOSTNAME", "shell-host")
    monkeypatch.setenv("TS_IMAGE", "shell-image")

    bash = shutil.which("bash") or "/bin/bash"
    completed = subprocess.run(
        [
            bash,
            str(repo / "scripts" / "waypoint_tailscale.sh"),
            "up",
            "profile-a",
        ],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WAYPOINTCTL_STATE_DIR": str(tmp_path / "state"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    text = log_file.read_text(encoding="utf-8")
    assert "run" in text
    assert "-e TS_AUTHKEY=tskey-auth-123" in text
    assert "-e TS_HOSTNAME=wp-profile-a" in text
    assert "tailscale/tailscale:stable" in text
    assert "--name waypoint-tailscale-profile-a" in text
    assert (
        f"-v {tmp_path / 'state' / 'tailscale' / 'profile-a'}:/var/lib/tailscale"
        in text
    )
    assert (
        "tailscale serve --bg --yes --http=3000 http://host.docker.internal:3000"
        in text
    )
    assert (
        "tailscale serve --bg --yes --http=8787 http://host.docker.internal:8787"
        in text
    )
    assert "tailscale container ready: waypoint-tailscale-profile-a" in completed.stdout


def test_helper_does_not_expand_shell_substitutions_in_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _make_repo(tmp_path / "repo")
    _copy_tailscale_script(repo)
    (repo / ".env").write_text(
        'TS_AUTHKEY="$(echo HACKED)"\n'
        "TS_HOSTNAME='waypoint-${USER}'\n"
        "TS_IMAGE=tailscale/tailscale:stable\n",
        encoding="utf-8",
    )
    log_file = tmp_path / "docker.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_docker(bin_dir, log_file)

    bash = shutil.which("bash") or "/bin/bash"
    completed = subprocess.run(
        [
            bash,
            str(repo / "scripts" / "waypoint_tailscale.sh"),
            "up",
            "profile-a",
        ],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WAYPOINTCTL_STATE_DIR": str(tmp_path / "state"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    text = log_file.read_text(encoding="utf-8")
    assert "-e TS_AUTHKEY=$(echo HACKED)" in text
    assert "-e TS_HOSTNAME=waypoint-${USER}" in text


def test_helper_rejects_degenerate_profile_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = _make_repo(tmp_path / "repo")
    _copy_tailscale_script(repo)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "docker.log"
    _write_fake_docker(bin_dir, log_file)

    bash = shutil.which("bash") or "/bin/bash"
    completed = subprocess.run(
        [bash, str(repo / "scripts" / "waypoint_tailscale.sh"), "status", "---"],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "WAYPOINTCTL_STATE_DIR": str(tmp_path / "state"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "invalid profile name: ---" in completed.stderr


def _path_without_docker() -> str:
    real_docker = shutil.which("docker")
    skip = str(Path(real_docker).parent) if real_docker else None
    return os.pathsep.join(
        entry
        for entry in os.environ["PATH"].split(os.pathsep)
        if entry and entry != skip
    )


def test_helper_requires_docker_when_binary_is_missing(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    bash = shutil.which("bash") or "/bin/bash"
    completed = subprocess.run(
        [
            bash,
            str(repo / "scripts" / "waypoint_tailscale.sh"),
            "down",
            "profile-a",
        ],
        cwd=repo,
        env={
            **os.environ,
            "PATH": _path_without_docker(),
            "WAYPOINTCTL_STATE_DIR": str(tmp_path / "state"),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "Docker is required for this helper." in completed.stderr
