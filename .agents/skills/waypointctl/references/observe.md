# Observing The Stack

Use:

```bash
waypointctl status
waypointctl logs backend
waypointctl logs frontend
waypointctl logs all
waypointctl doctor
```

`status` reports managed, stopped, or unmanaged services, each with a health
field — read the health, not just `running`, when deciding whether a service is
actually up.

`logs` **follows** the service log files like `tail -f`: it does not return on
its own and will block a foreground call indefinitely. Do not run it as a plain
blocking command. Instead:

- prefer `waypointctl status` to check health, or
- read the underlying log file directly with a bounded `tail -n <N>` (the log
  paths come from `waypointctl doctor` — see `env-state.md`), or
- run `waypointctl logs` detached / in the background if you genuinely need a
  live stream, and stop it when done.

For session-level state, use the `waypoint` skill and `waypoint sessions list`.
