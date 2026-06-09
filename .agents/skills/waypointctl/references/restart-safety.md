# Restart Safety

Before backend or full-stack restart:

1. Run `waypoint sessions list`.
2. Surface sessions that are `running` or `waiting_input`.
3. Ask the user to confirm interruption.
4. Prefer daemon-backed restart so the caller is not killed mid-command.

Commands:

```bash
waypointctl daemon start
waypointctl restart backend
waypointctl restart all
```

Avoid `--wait` from inside a hosted backend process unless the user understands
the caller may be interrupted. After restart, verify with:

```bash
waypointctl status
waypointctl logs backend
```
