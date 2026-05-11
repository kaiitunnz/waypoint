# waypointctl

`waypointctl` is the standalone Waypoint control plane. It supervises the
backend and frontend services without depending on `scripts/waypoint.sh`.

## Install

```
uv tool install ./waypointctl
# or
pipx install ./waypointctl
```

This installs two console scripts: `waypointctl` (the CLI) and `waypointd`
(the optional daemon).

## Commands

```
waypointctl --home <repo> start [backend|frontend|all]
waypointctl --home <repo> stop  [backend|frontend|all]
waypointctl --home <repo> restart [backend|frontend|all]
waypointctl --home <repo> status
waypointctl --home <repo> logs  [backend|frontend|all]
waypointctl daemon start | stop | status
waypointctl doctor
```

`--home` (or `WAYPOINT_HOME`) points at the Waypoint repository root.
If unset, waypointctl walks up from the current directory looking for
`backend/`, `frontend/`, and `scripts/`.

## State directory

State (PID files, logs, default data dirs) lives under
`WAYPOINTCTL_STATE_DIR`, defaulting to `~/.waypoint/`:

```
~/.waypoint/
├── run/           waypointd.{sock,pid}, {backend,frontend,caffeinate}.{pid,started-this-run}
├── logs/          waypointd.log, backend.log, frontend.log
├── backend-data/  (default; overridable via WAYPOINT_STACK_BACKEND_DATA_DIR)
└── uv-cache/      (default; overridable via WAYPOINT_STACK_UV_CACHE_DIR)
```

This is intentionally separate from `scripts/waypoint.sh`'s
repo-local `tmp/waypoint/`. The two tools do not share state by default;
point one at the other's directory with the env vars below if you want
them to.

## Environment overrides

All `WAYPOINT_STACK_*` knobs honored by the shell script are honored here
too. `.env` at the repo root is loaded via `python-dotenv` and merged
*over* the process environment, matching `set -a; source .env`. Shell
features like `$VAR` interpolation and `$(...)` substitution in `.env`
are not supported.

| Variable | Default | Effect |
| --- | --- | --- |
| `WAYPOINTCTL_STATE_DIR` | `~/.waypoint` | Root of the state tree. |
| `WAYPOINTCTL_DAEMON` | unset | When `1`, route every command through `waypointd` (auto-starting it if needed). |
| `WAYPOINT_STACK_BACKEND_HOST` | `0.0.0.0` | Backend bind host. |
| `WAYPOINT_STACK_BACKEND_PORT` | `8787` | Backend port. |
| `WAYPOINT_STACK_CONFIG` | `backend/waypoint.yaml` | Path to backend config (relative paths resolve under `--home`). |
| `WAYPOINT_STACK_BACKEND_DATA_DIR` | `<state-dir>/backend-data` | Backend data directory. |
| `WAYPOINT_STACK_FRONTEND_PORT` | `3000` | Frontend port. |
| `WAYPOINT_STACK_FRONTEND_DEV` | `0` | When `1`, start the frontend in `npm run dev` mode. |
| `WAYPOINT_STACK_START_TIMEOUT` | `30` | Seconds to wait for health probes. |
| `WAYPOINT_STACK_UV_CACHE_DIR` | `<state-dir>/uv-cache` | Backend `UV_CACHE_DIR`. |
| `WAYPOINT_STACK_FORCE_FRONTEND_BUILD` | `0` | When `1`, always rebuild the frontend before `npm run start`. |
| `WAYPOINT_STACK_CAFFEINATE` | `1` | macOS only: hold a `caffeinate -i -s` for the lifetime of the stack. |

## Daemon mode

By default `waypointctl` runs in-process: it spawns/manages the backend
and frontend itself, then exits. **`waypointd` is off and never
auto-spawns.** Start it explicitly when you want a long-lived
supervisor:

```
waypointctl daemon start
waypointctl daemon status
waypointctl daemon stop
```

Once `waypointd` is running, subsequent `waypointctl` commands route
through it transparently. Set `WAYPOINTCTL_DAEMON=1` to make every
command go through the daemon and auto-start it on first invocation.

### Why use the daemon

`waypointctl restart backend` issued from inside the backend's own
process tree (e.g., an agent session that Waypoint itself is hosting)
would kill the caller mid-restart in in-process mode. The CLI detects
this case and refuses with a pointer to `waypointctl daemon start`.
Running with the daemon avoids the problem entirely: the daemon owns
the backend's process group, so the agent's CLI invocation is in a
separate tree.

## Logs

`waypointctl logs` execs `tail -n 50 -f` against the log files in
`<state-dir>/logs/`. This is the only place waypointctl shells out to a
system tool for stack control.
