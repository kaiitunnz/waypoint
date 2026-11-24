"""Layout of the npm workspace at the repo root."""

from typing import Literal

FrontendVariant = Literal["v1", "v2"]

APP_DIRS: dict[FrontendVariant, str] = {"v1": "frontend", "v2": "frontend-v2"}
WEB_CORE_DIR = "packages/web-core"
ROOT_LOCKFILE = "package-lock.json"

WORKSPACE_MANIFESTS: tuple[str, ...] = (
    "package.json",
    *(f"{app_dir}/package.json" for app_dir in APP_DIRS.values()),
    f"{WEB_CORE_DIR}/package.json",
)


def parse_variant(raw: str | None) -> FrontendVariant:
    value = (raw or "v1").strip().lower()
    if value == "v1":
        return "v1"
    if value == "v2":
        return "v2"
    raise ValueError(f"WAYPOINT_STACK_FRONTEND must be v1 or v2, got {raw!r}")


def other_variant(variant: FrontendVariant) -> FrontendVariant:
    return "v2" if variant == "v1" else "v1"
