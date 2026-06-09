# Tailscale Profiles

Waypoint can manage Docker-backed Tailscale profiles:

```bash
waypointctl tailscale up <profile>
waypointctl tailscale down <profile>
waypointctl tailscale status <profile>
waypointctl tailscale logs <profile>
```

`up` needs `TS_AUTHKEY`. The helper performs Docker/Tailscale preflight checks.
If Docker is missing, `status` degrades gracefully but `up`, `down`, and `logs`
abort with an explanatory message.

Confirm before changing tailnet connectivity.
