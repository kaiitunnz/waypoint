# waypointctl

`waypointctl` is the standalone Waypoint control plane. It supervises the
backend and frontend services without depending on `scripts/waypoint.sh`.

## Install

```
uv tool install ./waypointctl
# or
pipx install ./waypointctl
```

This installs one console script: `waypointctl`. The optional daemon
(`waypointd`) is launched by `waypointctl daemon start` and runs as
`python -m waypointctl.daemon` under the hood.

## Commands

```
waypointctl --home <repo> start [backend|frontend|all]
waypointctl --home <repo> stop  [backend|frontend|all]
waypointctl --home <repo> restart [backend|frontend|all]
waypointctl --home <repo> status
waypointctl --home <repo> logs  [backend|frontend|all]
waypointctl daemon start | stop | restart | status
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
├── tailscale/     Docker-backed tailnet state (per-profile dirs + `active-profile` marker)
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
| `WAYPOINT_STACK_BACKEND_PORT` | `8787` | Backend port. Also forwarded to the frontend build as `NEXT_PUBLIC_BACKEND_PORT` so the picker infers the right URL; the cached build is invalidated when this changes. |
| `WAYPOINT_STACK_CONFIG` | `backend/waypoint.yaml` | Path to backend config (relative paths resolve under `--home`). |
| `WAYPOINT_STACK_BACKEND_DATA_DIR` | `<state-dir>/backend-data` | Backend data directory. |
| `WAYPOINT_STACK_FRONTEND_PORT` | `3000` | Frontend port. |
| `WAYPOINT_STACK_FRONTEND_DEV` | `0` | When `1`, start the frontend in `npm run dev` mode. |
| `WAYPOINT_STACK_START_TIMEOUT` | `30` | Seconds to wait for health probes. |
| `WAYPOINT_STACK_UV_CACHE_DIR` | `<state-dir>/uv-cache` | Backend `UV_CACHE_DIR`. |
| `WAYPOINT_STACK_FORCE_FRONTEND_BUILD` | `0` | When `1`, always rebuild the frontend before `npm run start`. |
| `WAYPOINT_STACK_CAFFEINATE` | `1` | macOS only: hold a `caffeinate -i -s` for the lifetime of the stack. |
| `WAYPOINT_STACK_CONTROL_HOST` | `0.0.0.0` | Bind host for the daemon's remote-control console. |
| `WAYPOINT_STACK_CONTROL_PORT` | `8799` | Port for the daemon's remote-control console; `0` disables it. |

## Multiple tailnets per host

`waypointctl tailscale` manages Docker-backed Tailscale profiles for hosts that must participate in multiple tailnets. Each profile maps to one container and one tailnet node:

```
waypointctl tailscale up <profile>
waypointctl tailscale down <profile>
waypointctl tailscale status <profile>
waypointctl tailscale logs <profile>
```

Copy the repo-root [`.env.example`](../.env.example) to `.env`, then set:

- `TS_AUTHKEY` for `tailscale up`
- `TS_HOSTNAME` to override the node name
- `TS_IMAGE` to override the container image
- `TS_HOST_ALIAS` to override the in-container alias for the host (defaults to `host.docker.internal`; change it on hosts where `--add-host …:host-gateway` resolves differently)

The helper reads the same repo-root `.env` that the rest of `waypointctl` loads.
Each profile keeps its Docker/Tailscale state under `~/.waypoint/tailscale/<profile>/`.

Each verb runs a preflight that checks whether Docker and Tailscale are installed locally:

- Docker installed: the verb runs. `up` additionally prompts for interactive confirmation when Tailscale is also installed, so you opt in to the Docker path rather than reaching for the host-level `tailscale` binary.
- Docker missing, command is `status`: degrade gracefully — print `docker not installed; no tailscale container on this host.` and exit 0. `status` is a read-only query and should not error on hosts that have never used the helper.
- Docker missing, command is `up`/`down`/`logs`: abort with a message identifying what's wrong. `logs` uses a verb-specific message; `up`/`down` say either "Docker is missing" (if Tailscale is present) or "install either Docker or Tailscale" (if neither is).

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

When `waypointd` is running, `restart` and `stop` are **deferred**:
the daemon acknowledges the request, closes its end of the socket,
and only then does the kill+start on a worker thread. The caller gets
its exit code before the backend (and any process descended from it)
is signalled, so a Waypoint-hosted agent can issue a restart and have
its `subprocess.run` return cleanly before its own process tree is
torn down.

Side effect of deferral: deferred commands don't stream progress logs
to the client. `restart`/`stop` reply `ok` immediately; the actual work
is visible in the daemon's own log (`<state-dir>/logs/waypointd.log`).
Use `waypointctl status` to inspect afterward. `start` and `status` stay
synchronous and stream output as before.

Pass `-w` / `--wait` to `stop` or `restart` to force the daemon to run
the command synchronously and stream its progress, the same way `start`
behaves. When `--wait` is set, the agent-restart safety check applies
even in daemon mode — the CLI can't be inside the target's process tree
because it stays on the wire across the kill.

`waypointctl daemon stop` stops only the daemon; backend and frontend were
spawned in their own sessions and keep running, so it doesn't bounce the
stack (the CLI hints at `waypointctl stop` if you meant to bring those down
too). It waits for the daemon to fully exit before returning — graceful
shutdown can take up to 30s while in-flight workers drain — so that
`daemon stop && daemon start` reliably replaces the daemon instead of racing
a half-exited one; pass `--no-wait` to skip the wait. To roll the daemon
onto new code, use `waypointctl daemon restart`.

### Out-of-band remote control

While the daemon runs it also serves an HTTP control console on
`WAYPOINT_STACK_CONTROL_PORT` (default `8799`, exposed over Tailscale beside
the backend and frontend ports). It lives in the supervisor rather than a
managed service, so it stays reachable when a corrupt frontend build or a
crashed backend has made the normal app unloadable — the break-glass path
for when the only thing to hand is a phone. It drives the same stack/process
layer `waypointctl` does and never touches the app's session data.

Open `http://<host>:8799/`, authenticate with `WAYPOINT_PASSWORD`, and you
get:

- **Status** — live health, pid, and port for backend and frontend, polled
  every few seconds so you can watch a restart come back up.
- **Logs** — the tail of `backend.log` / `frontend.log`, to see why a build
  broke before acting.
- **Lifecycle** — `restart` / `stop` / `start` a service or the whole stack,
  the same as `waypointctl <action> <target>`.
- **Redeploy** — restart the whole stack on one of three channels:
  - **stable** — check out the latest release tag, then restart.
  - **nightly** — check out the tip of `main`, then restart.
  - **current** — restart the checked-out tree with no git update; the only
    channel that works on a dirty or unmanaged checkout. Stable and nightly
    fail safe there, surfacing git's refusal and leaving the stack untouched.

Login exchanges the password for a short-lived bearer token, held only in
memory — a daemon restart drops it and you log in again. It issues one only
when a real `WAYPOINT_PASSWORD` is set (a blank or `change-me` value returns
`503`) and locks out after five failed attempts in a minute, so it can't
become an open surface on the tailnet. Mutating operations run on a worker
thread and return `202` at once; the page polls `/api/status` for the
outcome, so a 30–60s rebuild never holds a connection open, and one
operation runs at a time. Restarting from here is safe where the same
restart from inside a session is not, because the supervisor is the parent
of the service process groups, not a descendant. The action set is a fixed
allowlist with no arbitrary command execution, and every operation is logged
to `<state-dir>/logs/waypointd.log`.

The console exists only while `waypointd` runs. Expose it over Tailscale
only, never publicly; set `WAYPOINT_STACK_CONTROL_PORT=0` to disable it; and
run `waypointctl doctor` to print its URL and confirm it responds.

#### API

| Method · path | Auth | Purpose |
| --- | --- | --- |
| `GET /` | — | The console page. |
| `GET /health` | — | Liveness of the control plane itself. |
| `POST /api/login` | password | `{password}` → `{token, expires_in}`. |
| `GET /api/status` | token | Service status + `last_op`. |
| `GET /api/logs?target=&n=` | token | Tail of a service log. |
| `POST /api/action` | token | `{action, target}` → `202`; async. |
| `POST /api/redeploy` | token | `{channel: stable\|nightly\|current}` → `202`; async. |

## Logs

`waypointctl logs` execs `tail -n 50 -f` against the log files in
`<state-dir>/logs/`. This is the only place waypointctl shells out to a
system tool for stack control.
