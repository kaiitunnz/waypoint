import shlex

from waypoint.launch_targets import SshLaunchTargetConfig

# The script to run opencode serve on a remote host, parse the random port it
# binds to (since we pass --port=0), and output it via a __WP_PORT__ sentinel.
# The script blocks on `read` from stdin; when the SSH session closes, stdin
# breaks, and it kills the opencode server, preventing orphaned processes.
REMOTE_SERVE_SCRIPT = """
set -e

OPENCODE_BIN=${1:-opencode}
LOG=$(mktemp)

# Start OpenCode on an ephemeral port, sending output to the temp log.
"$OPENCODE_BIN" serve --hostname=127.0.0.1 --port=0 > "$LOG" 2>&1 &
PID=$!

# Wait for it to bind and output the port line.
PORT=""
for i in {1..50}; do
    PORT=$(grep -o "listening on http://127.0.0.1:[0-9]*" "$LOG" | grep -o "[0-9]*$" || true)
    if [ -n "$PORT" ]; then
        echo "__WP_PORT__=${PORT}"
        break
    fi
    sleep 0.1
done

if [ -z "$PORT" ]; then
    echo "Failed to start opencode serve:" >&2
    cat "$LOG" >&2
    rm -f "$LOG"
    kill -9 $PID 2>/dev/null || true
    exit 1
fi

rm -f "$LOG"

# Block until stdin is closed (SSH disconnects).
read -r _ || true

# Cleanup.
kill -9 $PID 2>/dev/null || true
"""


def build_remote_serve_args(
    target: SshLaunchTargetConfig,
    opencode_bin: str,
    cwd: str | None = None,
) -> tuple[str, ...]:
    cmd = ["bash", "-s", shlex.quote(opencode_bin)]
    return target.build_remote_exec_args(cmd, cwd)
