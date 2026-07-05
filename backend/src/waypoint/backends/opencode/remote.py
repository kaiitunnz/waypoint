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
    extra_args: tuple[str, ...] = (),
    launch_env: dict[str, str] | None = None,
) -> tuple[str, ...]:
    # Pass cwd as $1 so the script's clean (rcfile-free) subshell can cd
    # itself — outer-shell cd is fragile when user rcfiles wrap `cd`
    # (oh-my-bash plugins do). Extra CLI args follow as $2…$# and the
    # script `shift`s the cwd off so $@ ends up holding only those flags.
    cmd = ["bash", "-c", REMOTE_SERVE_SCRIPT, opencode_bin, cwd or "", *extra_args]
    return with_ssh_keepalive(
        target.build_remote_exec_args(cmd, None, extra_env=launch_env)
    )
