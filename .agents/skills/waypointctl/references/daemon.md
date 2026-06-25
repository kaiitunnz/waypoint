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
`WAYPOINT_STACK_CONTROL_PORT` (default `8799`, exposed over Tailscale next to the
backend/frontend ports). It lives in the supervisor, so it stays reachable when a
corrupt frontend build or a crashed backend has made the normal app unloadable —
the recovery path when AFK on a phone.

Open `http://<host>:8799/`, enter `WAYPOINT_PASSWORD`, and you get live status,
log tails, lifecycle (restart/stop/start per service or all), and redeploy
(stable / nightly / current channels) — the stack layer `waypointctl` already
owns, not the app's session UI. Login returns a
short-lived bearer token; mutating actions run async (`202` + poll `/api/status`).
It refuses to act unless a real password is set (blank/`change-me` → `503`) and
locks out after repeated failed logins. It exists **only** in daemon mode — not
in the default in-process mode. `waypointctl doctor` prints the control URL and
whether it responds. Disable with `WAYPOINT_STACK_CONTROL_PORT=0`.
