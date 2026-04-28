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
cp .env.example .env
# edit .env and set a real WAYPOINT_PASSWORD
uv sync --group dev
uv run waypoint serve
```

Waypoint uses the Python SDK from `../3rdparty/codex/sdk/python` for managed Codex app-server sessions.

To install the backend as a macOS LaunchAgent:

```bash
cd backend
./scripts/install_launchd.sh
```

Backend `.env`:

- `WAYPOINT_PASSWORD` — login password, defaults to `change-me`
- `WAYPOINT_HOST` — bind host; use `0.0.0.0` for phone/Tailscale access
- `WAYPOINT_PORT` — bind port, defaults to `8787`
- `WAYPOINT_DATA_DIR` — app data directory
- `WAYPOINT_CONFIG_PATH` — optional YAML startup config path

Backend config precedence:

- CLI flags such as `waypoint serve --host ... --port ... --config ...`
- `WAYPOINT_*` environment variables
- values from `backend/waypoint.yaml`
- built-in defaults

Remote Codex mode:

- Copy `backend/waypoint.example.yaml` to a local YAML file and set `WAYPOINT_CONFIG_PATH`, or run `uv run waypoint serve --config ./waypoint.yaml`.
- The YAML file can hold both core backend settings and `codex_remote` SSH settings.
- When `codex_remote.enabled` is true, managed Codex sessions launch through SSH on the configured host while Claude/tmux sessions stay local.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend dev notes:

- `npm run dev` binds to `0.0.0.0` so the phone can reach the dev server over Tailscale.
- The dev config auto-allows the machine's non-internal IPv4 addresses, including typical Tailscale `100.x.x.x` addresses, and emits both bare hosts and full `http://host:3000` origins.
- If you still need extra origins, create `frontend/.env.local` from `frontend/.env.example` and set `NEXT_ALLOWED_DEV_ORIGINS` as a comma-separated list.
- For the most reliable phone check-in flow, prefer `npm run build && npm run start` over `npm run dev`.

The frontend stores the backend base URL and bearer token in local storage.

When the frontend is opened from a phone, it now infers the backend URL from the current page host and port `8787`. Example:

- frontend: `http://100.x.y.z:3000`
- inferred backend: `http://100.x.y.z:8787`
