---
name: waypointctl
description: Use when managing the Waypoint stack with `waypointctl`, including start, stop, restart, status, logs, daemon mode, deploy/update flow, environment/state paths, frontend/backend service targets, and Docker-backed Tailscale profiles.
---

# Waypoint Stack

Use this skill for `waypointctl`, the local stack supervisor for Waypoint.
These commands affect the deployment the assistant is running on.

Start with `waypointctl status` for state. To learn the command surface, run
`waypointctl help` to dump every nested command with its arguments and options
in one call (`waypointctl help --json` for structured output). It is generated
from the command definitions, so it is ground truth for the installed version;
prefer it over per-level `--help`, and defer to it for exact flags rather than
trusting any list reproduced in this skill.

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
- `start`/`restart` are non-blocking: a service shown `stopped`/`starting` right
  after is the build in progress, not a failure. Poll `status` for health
  instead of concluding it failed (see `references/lifecycle.md`).
- `restart` deploys the **checked-out branch**, not `main`; confirm the checkout
  is current first or a stale branch silently drops merged fixes.
- `waypointctl logs` blocks like `tail -f` — never run it as a foreground step
  to "verify" a restart; use `status` (see `references/observe.md`).
