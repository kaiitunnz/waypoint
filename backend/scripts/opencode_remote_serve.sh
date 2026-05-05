#!/usr/bin/env bash

set -e

# Run `opencode serve` on a remote host, wait for it to announce the bound
# port, and print a Waypoint sentinel that the local launcher can parse.
# The script blocks on stdin; when the SSH session closes, stdin breaks and
# the OpenCode server is terminated so we do not leave orphaned processes.

_kill_proc() {
    local pid=$1
    [ -n "$pid" ] || return 0
    kill "$pid" 2>/dev/null || return 0
    local i
    for i in {1..20}; do
        sleep 0.1
        kill -0 "$pid" 2>/dev/null || return 0
    done
    kill -9 "$pid" 2>/dev/null || true
}

# Invoked via `bash -c SCRIPT BIN`, which assigns BIN to $0 (the script-name
# slot), not $1. Reading $0 here honors a configured remote_bin path.
OPENCODE_BIN=${0:-opencode}
LOG=$(mktemp)
PID=""

# Backstop: kill the OpenCode server on any exit path (clean exit,
# `read` returning, or a signal from SSH closing the channel) so we
# never leave the server orphaned on the remote host.
trap '_kill_proc "$PID"; rm -f "$LOG"' EXIT HUP INT TERM

"$OPENCODE_BIN" serve --hostname=127.0.0.1 --port=0 >"$LOG" 2>&1 &
PID=$!

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
    exit 1
fi

# Verify opencode is still alive after announcing its port.
sleep 0.3
if ! kill -0 "$PID" 2>/dev/null; then
    echo "opencode exited immediately after binding port ${PORT}:" >&2
    cat "$LOG" >&2
    exit 1
fi

read -r _ || true
