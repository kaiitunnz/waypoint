"""Connection-lifecycle knobs for the OpenCode backend.

All values come from ``WAYPOINT_OPENCODE_*`` env vars with conservative
defaults. Each is read on demand so the test suite can override them
inline. The defaults are tuned around two principles:

1. **No application-layer idle timeout on long-lived streams or
   long-running operations.** TCP/SSH keepalive is the only liveness
   signal — a quiet conversation is a feature, not a failure.
2. **Network death is detected by keepalive probes**, not by silence.
   ServerAliveInterval=30s + ServerAliveCountMax=6 → ~3 minutes from
   VPN drop to clean SSH exit; that's the floor.

Set any value to ``0`` to disable that layer. ``HTTP_CONTROL_TIMEOUT``
applies only to tiny control-plane requests; long-running calls
(summarize, etc.) are exempt and depend on TCP/SSH keepalive only.
"""

import os

DEFAULTS: dict[str, int] = {
    "WAYPOINT_OPENCODE_SSH_CONNECT_TIMEOUT": 15,
    "WAYPOINT_OPENCODE_SSH_KEEPALIVE_INTERVAL": 30,
    "WAYPOINT_OPENCODE_SSH_KEEPALIVE_COUNT": 6,
    "WAYPOINT_OPENCODE_HTTP_CONTROL_TIMEOUT": 60,
}


def _get_int(name: str) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return DEFAULTS[name]
    try:
        value = int(raw)
    except ValueError:
        return DEFAULTS[name]
    return max(value, 0)


def ssh_connect_timeout() -> int:
    return _get_int("WAYPOINT_OPENCODE_SSH_CONNECT_TIMEOUT")


def ssh_keepalive_interval() -> int:
    return _get_int("WAYPOINT_OPENCODE_SSH_KEEPALIVE_INTERVAL")


def ssh_keepalive_count() -> int:
    return _get_int("WAYPOINT_OPENCODE_SSH_KEEPALIVE_COUNT")


def http_control_timeout() -> int:
    return _get_int("WAYPOINT_OPENCODE_HTTP_CONTROL_TIMEOUT")


def ssh_keepalive_args() -> list[str]:
    """SSH ``-o`` flags for connect + keepalive timeouts.

    Splice between ``ssh_bin`` and ``ssh_destination`` in the argv so a
    dropped link surfaces as a clean SSH exit code at the keepalive
    deadline rather than wedging indefinitely on a half-open TCP socket.
    """
    args: list[str] = []
    connect = ssh_connect_timeout()
    if connect > 0:
        args += ["-o", f"ConnectTimeout={connect}"]
    interval = ssh_keepalive_interval()
    if interval > 0:
        args += ["-o", f"ServerAliveInterval={interval}"]
    count = ssh_keepalive_count()
    if count > 0:
        args += ["-o", f"ServerAliveCountMax={count}"]
    return args


def with_ssh_keepalive(args: tuple[str, ...]) -> tuple[str, ...]:
    """Inject SSH keepalive ``-o`` flags into an ssh argv tuple.

    ``args`` is the output of ``SshLaunchTargetConfig.build_remote_exec_args``
    where the first element is the ssh binary and the destination/command
    follow. The keepalive flags are inserted right after the binary so
    they take precedence over any user-configured ``-o`` in
    ``ssh_args``.
    """
    extra = ssh_keepalive_args()
    if not extra or not args:
        return args
    return (args[0], *extra, *args[1:])
