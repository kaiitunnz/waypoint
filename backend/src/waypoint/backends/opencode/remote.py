from pathlib import Path

from waypoint.backends.opencode.connection_config import with_ssh_keepalive
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
    # Pass cwd as a positional arg to the inner `bash -c SCRIPT BIN CWD` so
    # the cd happens inside the clean (rcfile-free) subshell. Doing the cd
    # in the outer `bash -ilc` is fragile when user rcfiles wrap `cd`
    # (oh-my-bash plugins do) — see the comment in opencode_remote_serve.sh.
    cmd = ["bash", "-c", REMOTE_SERVE_SCRIPT, opencode_bin, cwd or ""]
    return with_ssh_keepalive(target.build_remote_exec_args(cmd, None))
