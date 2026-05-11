import os
from pathlib import Path

import pytest

from waypointctl.config import apply_dotenv, load_env, load_stack_config


def test_load_env_merges_dotenv_over_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SHELL_ONLY", "shell")
    monkeypatch.setenv("BOTH", "from-shell")

    env_file = tmp_path / ".env"
    env_file.write_text("BOTH=from-dotenv\nDOTENV_ONLY=dotenv\n")

    merged = load_env(tmp_path)

    assert merged["SHELL_ONLY"] == "shell"
    assert merged["BOTH"] == "from-dotenv"
    assert merged["DOTENV_ONLY"] == "dotenv"


def test_load_env_without_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ONLY_SHELL", "yes")
    merged = load_env(tmp_path)
    assert merged["ONLY_SHELL"] == "yes"


def test_load_stack_config_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "repo"
    home.mkdir()

    config = load_stack_config(home, env={})

    assert config.home == home
    assert config.state_dir == (tmp_path / "state").resolve()
    assert config.backend_host == "0.0.0.0"
    assert config.backend_port == 8787
    assert config.backend_config == home / "backend" / "waypoint.yaml"
    assert config.backend_data_dir == (tmp_path / "state").resolve() / "backend-data"
    assert config.frontend_port == 3000
    assert config.frontend_dev is False
    assert config.start_timeout == 30
    assert config.uv_cache_dir == (tmp_path / "state").resolve() / "uv-cache"
    assert config.force_frontend_build is False
    assert config.caffeinate is True


def test_load_stack_config_env_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "repo"
    home.mkdir()
    env = {
        "WAYPOINT_STACK_BACKEND_HOST": "127.0.0.1",
        "WAYPOINT_STACK_BACKEND_PORT": "9090",
        "WAYPOINT_STACK_FRONTEND_PORT": "4000",
        "WAYPOINT_STACK_FRONTEND_DEV": "1",
        "WAYPOINT_STACK_START_TIMEOUT": "60",
        "WAYPOINT_STACK_FORCE_FRONTEND_BUILD": "true",
        "WAYPOINT_STACK_CAFFEINATE": "0",
    }

    config = load_stack_config(home, env=env)

    assert config.backend_host == "127.0.0.1"
    assert config.backend_port == 9090
    assert config.frontend_port == 4000
    assert config.frontend_dev is True
    assert config.start_timeout == 60
    assert config.force_frontend_build is True
    assert config.caffeinate is False


def test_load_stack_config_relative_path_resolves_under_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "repo"
    home.mkdir()

    config = load_stack_config(home, env={"WAYPOINT_STACK_CONFIG": "configs/alt.yaml"})

    assert config.backend_config == (home / "configs" / "alt.yaml").resolve()


def test_load_stack_config_absolute_path_kept_as_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(tmp_path / "state"))
    home = tmp_path / "repo"
    home.mkdir()
    external = tmp_path / "elsewhere" / "waypoint.yaml"

    config = load_stack_config(home, env={"WAYPOINT_STACK_CONFIG": str(external)})

    assert config.backend_config == external


def test_apply_dotenv_overrides_process_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BOTH", "from-shell")
    monkeypatch.delenv("FROM_DOTENV_ONLY", raising=False)

    (tmp_path / ".env").write_text("BOTH=from-dotenv\nFROM_DOTENV_ONLY=yes\n")
    apply_dotenv(tmp_path)

    assert os.environ["BOTH"] == "from-dotenv"
    assert os.environ["FROM_DOTENV_ONLY"] == "yes"


def test_apply_dotenv_no_op_without_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UNAFFECTED", "kept")
    apply_dotenv(tmp_path)
    assert os.environ["UNAFFECTED"] == "kept"
