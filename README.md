# Waypoint

Waypoint is a personal remote-control companion for Claude Code, Codex, and OpenCode sessions running on your machine. It provides a private, phone-first interface over Tailscale with:

- a unified session list
- chat-style transcript rendering
- raw terminal fallback
- managed launches for new sessions
- tmux attachment for existing sessions

Waypoint runs on macOS and Linux, and should also work under WSL when the local tooling and network access are available. Some convenience scripts are still OS-specific, for example the backend LaunchAgent helper is macOS-only.

## Issues

Use GitHub Issues directly for active bugs and feature requests.

- one leaf issue per actionable bug or feature request
- lowercase title prefixes: `bug: ...` and `feat: ...`
- label bug reports with `bug` and feature requests with `enhancement`

## Layout

- `backend/` — FastAPI daemon, tmux/session runtime, auth, persistence
- `backend/src/waypoint/backends/` — one package per coding agent (`claude_code/`, `claude_tty/`, `codex/`, `opencode/`), plus `tmux/` which is a shared transport; see [`docs/coding_agent_plugins.md`](docs/coding_agent_plugins.md) for the plugin contract and extension recipe
- `waypointctl/` — standalone control-plane package and daemon (`uv tool install ./waypointctl`, `pipx install ./waypointctl`)
- `frontend/` — Next.js PWA client
- `3rdparty/codex/` — pinned Codex submodule used for the local app-server SDK

A single long-lived **personal assistant** thread — which answers host questions, grounds itself in your running sessions, and manages them via the `waypoint sessions` CLI — can be enabled in `waypoint.yaml`. Its coding backend, model, and permission mode are switchable on the fly, and the thread can be terminated, reattached, or context-cleared from the assistant page; see [`docs/personal_assistant.md`](docs/personal_assistant.md).

## Supported agent versions

Waypoint speaks to Claude Code, Codex, and OpenCode through wire formats that change between releases (stream-json envelopes, codex app-server RPCs, hook-event shapes, REST/SSE event streams). Bumps outside the tested range are likely to work but are not guaranteed.

| Agent       | Tested versions   | Wire entry point                                                     | Notes                                                                                       |
| ----------- | ----------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Claude Code | `2.1.157` | `claude -p --input-format=stream-json --output-format=stream-json --permission-prompt-tool stdio` | Approvals ride the `can_use_tool` control protocol; `CLAUDE_CODE_WORKFLOWS=1` enables the Workflow tool. Also relies on `--include-hook-events`, the `system/status`/`compact_boundary` events, and `--session-id`/`--resume`. |
| Codex CLI   | `0.125.0`-`0.129.0` | `codex app-server --listen stdio://`                                 | Driven via the vendored Python SDK in `3rdparty/codex/sdk/python` (`thread_*` / `turn_*` RPCs). |
| Codex SDK   | `0.116.0a1`       | `3rdparty/codex/` submodule pin                                      | Bumped together with Codex CLI; track via `git submodule update --remote 3rdparty/codex`.   |
| OpenCode   | `1.14.30`        | `opencode serve` with REST + SSE API                             | HTTP-based; discovers models from `/provider`. |

To extend this matrix:

1. Update the row above with the new tested version.
2. Re-run the integration paths that touch the wire format:
   - Claude: `backend/src/waypoint/backends/claude_code/normalize.py` (status / compact / rate-limit / approval / content-block helpers) and `adapter.py`'s `_handle_*` stream handlers, plus `_handle_can_use_tool` for tool approval over the control protocol; remote launch in `backends/claude_code/remote.py::_build_remote_claude_command`.
   - Codex: `backend/src/waypoint/backends/codex/normalize.py::map_notification` and the SDK calls in `backends/codex/adapter.py::CodexAppServerAdapter`.
   - OpenCode: `backend/src/waypoint/backends/opencode/normalize.py::map_event` and the HTTP/SSE adapter in `backends/opencode/adapter.py::OpenCodeAdapter`.
3. If a bump breaks an event shape, prefer adding a branch in the relevant `normalize.py` over hard-pinning.

Adding a brand-new coding agent (Aider, …) is its own flow — the runtime, API, and frontend dispatch by plugin id, so a new backend is "implement [`BackendPlugin`](backend/src/waypoint/backends/base.py) and register it." See [`docs/coding_agent_plugins.md`](docs/coding_agent_plugins.md) for the contract, capability descriptor, and a step-by-step recipe.

## Quick start

Initialize the Codex submodule before syncing backend dependencies:

```bash
git submodule update --init --recursive
```

### Backend

```bash
cd backend
cp waypoint.example.yaml waypoint.yaml
# edit waypoint.yaml and set a real password
uv sync --group dev
uv run pre-commit install
uv run waypoint serve
```

Waypoint uses the Python SDK from `../3rdparty/codex/sdk/python` for managed Codex app-server sessions.

For a full backend quality pass before committing, run:

```bash
cd backend
uv run pre-commit run --all-files
```

To install the backend as a macOS LaunchAgent:

```bash
cd backend
./scripts/install_launchd.sh
```

Backend config precedence:

- CLI flags such as `waypoint --config ... serve --host ... --port ...` (`--config` is a top-level option, before the command)
- `WAYPOINT_*` environment variables (set in your shell, systemd unit, or LaunchAgent)
- values from `backend/waypoint.yaml`
- built-in defaults

The YAML file is the canonical place for settings; env vars are an escape hatch for machine-specific overrides. See `backend/waypoint.example.yaml` for the full schema, including optional named `ssh_targets` that add remote coding backends to the frontend picker.

The frontend launch form also reads `default_backend` and `default_cwd` from backend config through `/api/me`, so switching to a different Waypoint backend updates those defaults automatically. If the selected Waypoint host exposes SSH targets, those appear in the same picker and only affect new managed launches on that host.

When an SSH target is selected, the launch form uses that target's `default_cwd` as the default remote path and lets you override it per launch. Managed sessions now use the same `cwd` value for UI display and the actual SSH-side working directory.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend dev notes:

- `npm run dev` binds to `0.0.0.0` so the phone can reach the dev server over Tailscale.
- The dev config auto-allows the machine's non-internal IPv4 addresses, including typical Tailscale `100.x.x.x` addresses, and emits both bare hosts and full `http://host:3000` origins.
- If you still need extra origins, set `NEXT_ALLOWED_DEV_ORIGINS` (comma-separated) in your shell or in a `frontend/.env.local` you create yourself.
- For the most reliable phone check-in flow, prefer `npm run build && npm run start` over `npm run dev`.

The frontend stores the backend base URL and bearer token in local storage.

When the frontend is opened from a phone, it now infers the backend URL from the current page host and port `8787`. Example:

- frontend: `http://100.x.y.z:3000`
- inferred backend: `http://100.x.y.z:8787`

### Multiple tailnets per host

If one host must participate in more than one tailnet, run one Docker-backed Tailscale profile per tailnet. Each profile is a separate container and a separate Tailscale node.

See [waypointctl/README.md](waypointctl/README.md) for the command syntax, configuration variables, and startup checks.

## Local stack supervisor

`waypointctl` is the canonical way to run the local stack. It's an installable
Python control plane that supervises backend + frontend without depending on
the legacy shell script. Install it once:

```bash
uv tool install ./waypointctl
# or: pipx install ./waypointctl
```

Then from the repo root (or anywhere, with `--home <repo>`):

```bash
waypointctl start                   # bring up backend + frontend, exits when ready
waypointctl status                  # pid, port, health for each service
waypointctl logs                    # tail backend + frontend logs
waypointctl restart [backend|frontend|all]
waypointctl stop
waypointctl daemon start            # opt in to the long-lived waypointd supervisor
```

What it does:

- starts `uv run waypoint serve` in `backend/` (in its own process group)
- builds the frontend if needed, then runs `npm run start` in `frontend/`
- waits for backend `http://127.0.0.1:8787/health` and the frontend root URL
  before reporting success
- stores PID files, logs, and default backend/uv-cache directories under
  `WAYPOINTCTL_STATE_DIR` (defaults to `~/.waypoint/`)

Configuration comes from a repo-root `.env` file if present:

```bash
cp .env.example .env
```

Useful overrides (set in `.env` or per invocation):

```bash
WAYPOINT_STACK_BACKEND_PORT=8788 WAYPOINT_STACK_FRONTEND_PORT=3001 waypointctl start
WAYPOINT_STACK_CONFIG=/absolute/path/to/waypoint.yaml waypointctl restart
WAYPOINT_STACK_BACKEND_DATA_DIR=/tmp/waypoint-data waypointctl start
```

By default each command runs in-process and exits when the work is done.
`waypointctl daemon start` brings up a long-lived `waypointd` that subsequent
commands transparently route through; this is useful when an agent hosted by
Waypoint itself needs to issue `restart` without taking its own process tree
down. See [`waypointctl/README.md`](waypointctl/README.md) for the daemon
model, agent-restart safety check, and full env-var reference.

For a machine-managed background backend on macOS, `backend/scripts/install_launchd.sh`
remains the right tool; treat the frontend separately.

### Legacy: `scripts/waypoint.sh`

The shell-based supervisor is preserved for users on the existing workflow
and will be **deprecated** once we're confident `waypointctl` covers all
their cases. It writes state under `tmp/waypoint/` (repo-local) and the two
tools do not share state by default. New work should target `waypointctl`.

```bash
./scripts/waypoint.sh start | stop | restart | status | logs
```
