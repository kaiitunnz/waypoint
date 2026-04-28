# Waypoint Backend

FastAPI daemon for managing and observing Claude Code and Codex sessions.

Managed Codex sessions use the local Codex App Server SDK from `../3rdparty/codex/sdk/python`. Claude and attached sessions still use tmux.

## Development

```bash
git submodule update --init --recursive
cp .env.example .env
# edit .env
uv sync --group dev
uv run waypoint serve
```
