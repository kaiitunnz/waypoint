# Observing The Stack

Use:

```bash
waypointctl status
waypointctl logs backend
waypointctl logs frontend
waypointctl logs all
waypointctl doctor
```

`status` reports managed, stopped, or unmanaged services. `logs` follows the
service log files and is useful after start/restart or when a health check
fails.

For session-level state, use the `waypoint` skill and `waypoint sessions list`.
