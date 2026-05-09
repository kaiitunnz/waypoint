# Waypoint

Waypoint is a personal remote-control companion for Claude Code, Codex, and OpenCode sessions running on your Mac. It provides a private, phone-first interface over Tailscale with:

- a unified session list
- chat-style transcript rendering
- raw terminal fallback
- managed launches for new sessions
- tmux attachment for existing sessions

## Issue Tracking

GitHub Issues are the source of truth for active bugs and feature requests. Create leaf issues directly; tracking issues are no longer used.

Use this convention when opening issues:

- one leaf issue per actionable bug or feature request
- lowercase title prefixes: `bug: ...` and `feature request: ...`
- label bug reports with `bug` and feature requests with `enhancement`

## Layout

- `backend/` — FastAPI daemon, tmux/session runtime, auth, persistence
- `backend/src/waypoint/backends/` — one package per coding agent (`claude_code/`, `codex/`, `opencode/`, `tmux/`); see [`docs/coding_agent_plugins.md`](docs/coding_agent_plugins.md) for the plugin contract and extension recipe
- `frontend/` — Next.js PWA client
- `3rdparty/codex/` — pinned Codex submodule used for the local app-server SDK

## Supported agent versions

Waypoint speaks to Claude Code, Codex, and OpenCode through wire formats that change between releases (stream-json envelopes, codex app-server RPCs, hook-event shapes, REST/SSE event streams). Bumps outside the tested range are likely to work but are not guaranteed.

| Agent       | Tested versions   | Wire entry point                                                     | Notes                                                                                       |
| ----------- | ----------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Claude Code | `2.1.136`         | `claude -p --input-format=stream-json --output-format=stream-json`   | Relies on `--include-hook-events`, the `system/status`/`compact_boundary` events, and `--session-id`/`--resume`. |
| Codex CLI   | `0.129.0`         | `codex app-server --listen stdio://`                                 | Driven via the vendored Python SDK in `3rdparty/codex/sdk/python` (`thread_*` / `turn_*` RPCs). |
| Codex SDK   | `0.116.0a1`       | `3rdparty/codex/` submodule pin                                      | Bumped together with Codex CLI; track via `git submodule update --remote 3rdparty/codex`.   |
| OpenCode   | `1.14.30`        | `opencode serve` with REST + SSE API                             | HTTP-based; discovers models from `/provider`. |

To extend this matrix:

1. Update the row above with the new tested version.
2. Re-run the integration paths that touch the wire format:
   - Claude: `backend/src/waypoint/backends/claude_code/normalize.py` (status / compact / rate-limit / approval / content-block helpers) and `adapter.py`'s `_handle_*` stream handlers; hook bootstrap in `backends/claude_code/runtime_hook.py` + `server_config.py::_build_remote_claude_command`.
   - Codex: `backend/src/waypoint/backends/codex/normalize.py::map_notification` and the SDK calls in `backends/codex/adapter.py::CodexAppServerAdapter`.
   - OpenCode: `backend/src/waypoint/backends/opencode/normalize.py::map_event` and the HTTP/SSE adapter in `backends/opencode/adapter.py::OpenCodeAdapter`.
3. If a bump breaks an event shape, prefer adding a branch in the relevant `normalize.py` over hard-pinning — the goal is for the matrix to grow, not to fork on version.

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

- CLI flags such as `waypoint serve --host ... --port ... --config ...`
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

## Local stack supervisor

For a repo-local workflow that feels closer to `docker compose up/down`, use:

```bash
./scripts/waypoint.sh start
./scripts/waypoint.sh status
./scripts/waypoint.sh logs
./scripts/waypoint.sh stop
```

The script:

- starts `uv run waypoint serve` in `backend/`
- runs `npm run build` and then starts `npm run start` in `frontend/`
- stores PID files, logs, backend runtime data, and a local `uv` cache under `tmp/waypoint/`
- waits for backend `http://127.0.0.1:8787/health` and the frontend root URL before reporting success

Supported commands are `start`, `stop`, `restart`, `status`, and `logs [backend|frontend]`.

Configuration comes from a repo-root `.env` file if present. Start from:

```bash
cp .env.example .env
```

Useful overrides:

```bash
WAYPOINT_STACK_BACKEND_PORT=8788 WAYPOINT_STACK_FRONTEND_PORT=3001 ./scripts/waypoint.sh start
WAYPOINT_STACK_CONFIG=/absolute/path/to/waypoint.yaml ./scripts/waypoint.sh restart
WAYPOINT_STACK_BACKEND_DATA_DIR=/tmp/waypoint-data ./scripts/waypoint.sh start
```

This is intended for local development. If you want a machine-managed background service on macOS, keep using `backend/scripts/install_launchd.sh` for the backend and treat the frontend separately.
