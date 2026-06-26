# Daemon Mode

`waypointd` lets hosted agents ask for stop/restart without being killed before
the command is accepted.

```bash
waypointctl daemon start
waypointctl daemon status
waypointctl daemon stop
waypointctl daemon restart
```

`waypointctl daemon stop` stops only the daemon, not backend/frontend services
(use `waypointctl stop` for those), and waits for it to fully exit before
returning, so `daemon stop && daemon start` reliably replaces it. To roll the
daemon onto new code, use `waypointctl daemon restart`.

In daemon mode, service `stop`/`restart` may return before the work finishes;
check `waypointctl status` and the logs afterward.

## Out-of-band remote control

While `waypointd` runs, it serves an HTTP control console (default port `8799`,
over Tailscale) for driving the stack from a remote device when a broken frontend
build or backend has made the app unreachable. Open `http://<host>:8799/`,
authenticate with `WAYPOINT_PASSWORD`, and you get status, log tails, lifecycle
(restart/stop/start), and redeploy (stable/nightly/current) — what `waypointctl`
controls, not the app's session data. It exists only in daemon mode;
`waypointctl doctor` prints the URL and `WAYPOINT_STACK_CONTROL_PORT=0` disables
it.
