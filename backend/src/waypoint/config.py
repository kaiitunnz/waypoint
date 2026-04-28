from pathlib import Path
import os

from pydantic import BaseModel, Field
from dotenv import load_dotenv


BACKEND_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_ROOT / ".env")


def default_data_dir() -> Path:
    override = os.environ.get("WAYPOINT_DATA_DIR")
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    return Path.home() / "Library" / "Application Support" / "Waypoint"


DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def parse_cors_origins() -> list[str]:
    raw = os.environ.get("WAYPOINT_CORS_ORIGINS")
    if raw is None:
        return list(DEFAULT_CORS_ORIGINS)
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    return parts or list(DEFAULT_CORS_ORIGINS)


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
    cors_origins: list[str] = Field(default_factory=parse_cors_origins)
    cors_allow_origin_regex: str | None = Field(
        default_factory=lambda: os.environ.get("WAYPOINT_CORS_ORIGIN_REGEX")
    )

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_name

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / self.sessions_dir_name

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
