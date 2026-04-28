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

## Quick start

### Backend

```bash
cd backend
cp .env.example .env
# edit .env and set a real WAYPOINT_PASSWORD
uv sync --group dev
uv run waypoint serve
```

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
