# Daemon Mode

`waypointd` lets hosted agents ask for stop/restart without being killed before
the command is accepted.

```bash
waypointctl daemon start
waypointctl daemon status
waypointctl daemon stop
```

`waypointctl daemon stop` stops only the daemon, not backend/frontend services.
Use `waypointctl stop` for services.

When daemon mode is active, stop/restart may return before work finishes. Check
`waypointctl status` and logs afterward.
