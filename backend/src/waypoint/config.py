from pathlib import Path
import os

from pydantic import BaseModel, Field


def default_data_dir() -> Path:
    override = os.environ.get("WAYPOINT_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / "Waypoint"


class Settings(BaseModel):
    host: str = Field(default_factory=lambda: os.environ.get("WAYPOINT_HOST", "127.0.0.1"))
    port: int = Field(default_factory=lambda: int(os.environ.get("WAYPOINT_PORT", "8787")))
    password: str = Field(default_factory=lambda: os.environ.get("WAYPOINT_PASSWORD", "change-me"))
    data_dir: Path = Field(default_factory=default_data_dir)
    sessions_dir_name: str = "sessions"
    database_name: str = "waypoint.db"
    token_ttl_seconds: int = 60 * 60 * 24 * 7
    stream_poll_interval: float = 1.0
    tail_snapshot_lines: int = 200

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_name

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / self.sessions_dir_name

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
