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
uv sync --group dev
uv run waypoint serve
```

To install the backend as a macOS LaunchAgent:

```bash
cd backend
./scripts/install_launchd.sh
```

Environment variables:

- `WAYPOINT_PASSWORD` — login password, defaults to `change-me`
- `WAYPOINT_HOST` — bind host, defaults to `127.0.0.1`
- `WAYPOINT_PORT` — bind port, defaults to `8787`
- `WAYPOINT_DATA_DIR` — override app data directory

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend stores the backend base URL and bearer token in local storage.
