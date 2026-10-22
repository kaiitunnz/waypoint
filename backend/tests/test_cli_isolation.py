"""Keep inherited CLI settings confined to a temporary decoy database."""

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "test_name",
    [
        "test_rebuild_telemetry_refuses_when_database_write_locked",
        "test_rebuild_telemetry_real_lock_probe_passes_when_unlocked",
    ],
)
def test_cli_lock_tests_preserve_host_database(tmp_path: Path, test_name: str) -> None:
    host_data = tmp_path / "host-data"
    host_data.mkdir()
    host_db = host_data / "waypoint.db"
    with closing(sqlite3.connect(host_db)) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES ('untouched')")
        connection.commit()
    original_bytes = host_db.read_bytes()
    original_files = set(host_data.iterdir())
    host_config = tmp_path / "host.yaml"
    host_config.write_text(f"data_dir: {host_data}\n")

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("WAYPOINT_")
    }
    env.pop("PYTEST_ADDOPTS", None)
    env.update(
        WAYPOINT_DATA_DIR=str(host_data),
        WAYPOINT_CONFIG_PATH=str(host_config),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            f"{Path(__file__).with_name('test_cli.py')}::{test_name}",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "child-pytest"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert host_db.read_bytes() == original_bytes, "CLI test modified the host database"
    assert set(host_data.iterdir()) == original_files
    assert result.returncode == 0, result.stdout + result.stderr
