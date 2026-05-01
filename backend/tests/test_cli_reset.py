from pathlib import Path

from waypoint.cli import run_reset
from waypoint.settings import Settings


def _seed(tmp_path: Path) -> Settings:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    settings.database_path.write_bytes(b"SQLite format 3\x00")
    (settings.database_path.with_suffix(".db-wal")).write_bytes(b"wal")
    (settings.database_path.with_suffix(".db-shm")).write_bytes(b"shm")
    nested = settings.sessions_dir / "session-1"
    nested.mkdir()
    (nested / "raw.log").write_text("hello", encoding="utf-8")
    return settings


def test_reset_dry_run_leaves_data_intact(tmp_path: Path, capsys) -> None:
    settings = _seed(tmp_path)

    run_reset(settings, confirmed=False)

    captured = capsys.readouterr().out
    assert "Would remove" in captured
    assert "--yes" in captured
    assert settings.database_path.exists()
    assert settings.sessions_dir.exists()
    assert (settings.sessions_dir / "session-1" / "raw.log").exists()


def test_reset_confirmed_wipes_data_and_recreates_dirs(tmp_path: Path, capsys) -> None:
    settings = _seed(tmp_path)

    run_reset(settings, confirmed=True)

    captured = capsys.readouterr().out
    assert "removed" in captured
    assert "recreated" in captured
    assert not settings.database_path.exists()
    assert not settings.database_path.with_suffix(".db-wal").exists()
    assert not settings.database_path.with_suffix(".db-shm").exists()
    assert settings.sessions_dir.exists()
    assert not any(settings.sessions_dir.iterdir())


def test_reset_no_data_is_a_noop(tmp_path: Path, capsys) -> None:
    settings = Settings(data_dir=tmp_path / "data")

    run_reset(settings, confirmed=True)

    captured = capsys.readouterr().out
    assert "Nothing to reset" in captured
