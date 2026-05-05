#!/usr/bin/env bash

set -e

# Run `opencode serve` on a remote host, wait for it to announce the bound
# port, and print a Waypoint sentinel that the local launcher can parse.
# The script blocks on stdin; when the SSH session closes, stdin breaks and
# the OpenCode server is terminated so we do not leave orphaned processes.

# Invoked via `bash -c SCRIPT BIN`, which assigns BIN to $0 (the script-name
# slot), not $1. Reading $0 here honors a configured remote_bin path.
OPENCODE_BIN=${0:-opencode}
LOG=$(mktemp)

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

_kill_proc() {
    local pid=$1
    kill "$pid" 2>/dev/null || return
    local i
    for i in {1..20}; do
        sleep 0.1
        kill -0 "$pid" 2>/dev/null || return
    done
    kill -9 "$pid" 2>/dev/null || true
}

if [ -z "$PORT" ]; then
    echo "Failed to start opencode serve:" >&2
    cat "$LOG" >&2
    rm -f "$LOG"
    _kill_proc "$PID"
    exit 1
fi

# Verify opencode is still alive after announcing its port.
sleep 0.3
if ! kill -0 "$PID" 2>/dev/null; then
    echo "opencode exited immediately after binding port ${PORT}:" >&2
    cat "$LOG" >&2
    rm -f "$LOG"
    exit 1
fi

rm -f "$LOG"

read -r _ || true

_kill_proc "$PID"
