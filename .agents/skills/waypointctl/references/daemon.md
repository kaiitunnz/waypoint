# Daemon Mode

`waypointd` lets hosted agents ask for stop/restart without being killed before
the command is accepted.

```bash
waypointctl daemon start
waypointctl daemon status
waypointctl daemon stop
waypointctl daemon restart
```

`waypointctl daemon stop` stops only the daemon, not backend/frontend services.
Use `waypointctl stop` for services.

`daemon stop` waits for the daemon to fully exit before returning, so
`daemon stop && daemon start` reliably replaces it. To apply a code change to
the daemon itself, use `waypointctl daemon restart`.

When daemon mode is active, stop/restart may return before work finishes. Check
`waypointctl status` and logs afterward.

## Out-of-band remote control

While `waypointd` runs it serves an HTTP control console on
`WAYPOINT_STACK_CONTROL_PORT` (default `8799`, exposed over Tailscale beside the
backend/frontend ports). Living in the supervisor, it stays reachable when a
corrupt frontend build or a crashed backend has made the app unloadable — the
recovery path when AFK on a phone.

Open `http://<host>:8799/`, authenticate with `WAYPOINT_PASSWORD`, and drive the
stack: live status, log tails, lifecycle (restart/stop/start per service or all),
and redeploy on stable/nightly/current channels — what `waypointctl` controls,
not the app's session data. Login returns a short-lived bearer token and mutating
actions run async (`202`, then poll `/api/status`). It acts only with a real
password set (blank/`change-me` → `503`), locks out after repeated failures, and
exists only in daemon mode. `waypointctl doctor` prints the URL;
`WAYPOINT_STACK_CONTROL_PORT=0` disables it.
