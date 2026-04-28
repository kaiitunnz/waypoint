# Waypoint

Waypoint is a personal remote-control companion for Claude Code and Codex sessions running on your Mac. It provides a private, phone-first interface over Tailscale with:

- a unified session list
- chat-style transcript rendering
- raw terminal fallback
- managed launches for new sessions
- tmux attachment for existing sessions

## Layout

- `backend/` — FastAPI daemon, tmux/session runtime, auth, persistence
- `frontend/` — Next.js PWA client
- `3rdparty/codex/` — pinned Codex submodule used for the local app-server SDK

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
uv run waypoint serve
```

Waypoint uses the Python SDK from `../3rdparty/codex/sdk/python` for managed Codex app-server sessions.

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

When an SSH target is selected, the launch form uses that target's `default_remote_cwd` as the default remote path and lets you override it per launch. The session still keeps the local `cwd` separately for repo metadata and UI display.

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
