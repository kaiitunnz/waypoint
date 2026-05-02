#!/usr/bin/env bash

set -e

# Run `opencode serve` on a remote host, wait for it to announce the bound
# port, and print a Waypoint sentinel that the local launcher can parse.
# The script blocks on stdin; when the SSH session closes, stdin breaks and
# the OpenCode server is terminated so we do not leave orphaned processes.

OPENCODE_BIN=${1:-opencode}
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

if [ -z "$PORT" ]; then
    echo "Failed to start opencode serve:" >&2
    cat "$LOG" >&2
    rm -f "$LOG"
    kill -9 "$PID" 2>/dev/null || true
    exit 1
fi

rm -f "$LOG"

read -r _ || true

kill -9 "$PID" 2>/dev/null || true
