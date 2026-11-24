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
    assert config.control_host == "0.0.0.0"
    assert config.control_port == 8799
    assert config.frontend_variant == "v1"
    assert config.frontend_preview_port is None


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
        "WAYPOINT_STACK_CONTROL_HOST": "127.0.0.1",
        "WAYPOINT_STACK_CONTROL_PORT": "9099",
    }

    config = load_stack_config(home, env=env)

    assert config.backend_host == "127.0.0.1"
    assert config.backend_port == 9090
    assert config.frontend_port == 4000
    assert config.frontend_dev is True
    assert config.start_timeout == 60
    assert config.force_frontend_build is True
    assert config.caffeinate is False
    assert config.control_host == "127.0.0.1"
    assert config.control_port == 9099


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


def _config_for(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: dict[str, str]):
    monkeypatch.setenv("WAYPOINTCTL_STATE_DIR", str(tmp_path / "state"))
    return load_stack_config(tmp_path, env=env)


@pytest.mark.parametrize(
    ("raw", "expected"), [("v1", "v1"), ("v2", "v2"), (" V2 ", "v2"), ("", "v1")]
)
def test_frontend_variant_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str, expected: str
) -> None:
    config = _config_for(monkeypatch, tmp_path, {"WAYPOINT_STACK_FRONTEND": raw})
    assert config.frontend_variant == expected


def test_frontend_variant_rejects_unknown_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="WAYPOINT_STACK_FRONTEND"):
        _config_for(monkeypatch, tmp_path, {"WAYPOINT_STACK_FRONTEND": "v3"})


def test_frontend_preview_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _config_for(
        monkeypatch, tmp_path, {"WAYPOINT_STACK_FRONTEND_PREVIEW_PORT": "3011"}
    )
    assert config.frontend_preview_port == 3011


@pytest.mark.parametrize("raw", ["", "  "])
def test_blank_frontend_preview_port_disables_the_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str
) -> None:
    config = _config_for(
        monkeypatch, tmp_path, {"WAYPOINT_STACK_FRONTEND_PREVIEW_PORT": raw}
    )
    assert config.frontend_preview_port is None


@pytest.mark.parametrize("raw", ["0", "-1", "70000"])
def test_frontend_preview_port_out_of_range(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str
) -> None:
    with pytest.raises(ValueError, match="out of range"):
        _config_for(
            monkeypatch, tmp_path, {"WAYPOINT_STACK_FRONTEND_PREVIEW_PORT": raw}
        )


@pytest.mark.parametrize(
    ("env", "colliding"),
    [
        ({"WAYPOINT_STACK_FRONTEND_PORT": "4000"}, "WAYPOINT_STACK_FRONTEND_PORT"),
        ({"WAYPOINT_STACK_BACKEND_PORT": "4000"}, "WAYPOINT_STACK_BACKEND_PORT"),
    ],
)
def test_frontend_preview_port_must_not_collide(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env: dict[str, str],
    colliding: str,
) -> None:
    with pytest.raises(ValueError, match=colliding):
        _config_for(
            monkeypatch,
            tmp_path,
            {**env, "WAYPOINT_STACK_FRONTEND_PREVIEW_PORT": "4000"},
        )
