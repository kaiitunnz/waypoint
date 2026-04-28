# Waypoint Backend

FastAPI daemon for managing and observing Claude Code and Codex sessions through tmux.

## Development

```bash
cp .env.example .env
# edit .env
uv sync --group dev
uv run waypoint serve
```
