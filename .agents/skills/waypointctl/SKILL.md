---
name: waypointctl
description: Use when managing the Waypoint stack with `waypointctl`, including start, stop, restart, status, logs, daemon mode, deploy/update flow, environment/state paths, frontend/backend service targets, and Docker-backed Tailscale profiles.
---

# Waypoint Stack

Use this skill for `waypointctl`, the local stack supervisor for Waypoint.
These commands affect the deployment the assistant is running on.

Start with `waypointctl status` for state and `waypointctl --help` or a
subcommand's `--help` when the installed command surface is uncertain.

## Common Routing

- Observe status or logs: see `references/observe.md`.
- Start, stop, or restart services: see `references/lifecycle.md`.
- Handle backend/full-stack restart safely: see `references/restart-safety.md`.
- Pull/update/redeploy the app: see `references/update-deploy.md`.
- Manage `waypointd`: see `references/daemon.md`.
- Manage Docker-backed tailnet profiles: see `references/tailscale.md`.
- Understand env vars and state paths: see `references/env-state.md`.

## Guardrails

- Confirm before stopping or restarting backend/all services.
- A backend restart can interrupt every running session, including this
  assistant.
- Check active sessions with `waypoint sessions list` before backend or
  full-stack restarts.
- Prefer `waypointctl restart frontend` for frontend-only changes.
