from pathlib import Path

from waypoint.launch_targets import SshLaunchTargetConfig

_REMOTE_SERVE_SCRIPT_PATH = (
    Path(__file__).resolve().parents[4] / "scripts" / "opencode_remote_serve.sh"
)

REMOTE_SERVE_SCRIPT = _REMOTE_SERVE_SCRIPT_PATH.read_text(encoding="utf-8")


def build_remote_serve_args(
    target: SshLaunchTargetConfig,
    opencode_bin: str,
    cwd: str | None = None,
) -> tuple[str, ...]:
    cmd = ["bash", "-c", REMOTE_SERVE_SCRIPT, opencode_bin]
    return target.build_remote_exec_args(cmd, cwd)
