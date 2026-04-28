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

## Configuration

Waypoint now supports a single YAML config file for backend defaults. Start from `waypoint.example.yaml`, then either set `WAYPOINT_CONFIG_PATH` in `backend/.env` or start with:

```bash
uv run waypoint serve --config ./waypoint.yaml
```

Precedence is:

- CLI flags such as `--host`, `--port`, and `--config`
- environment variables such as `WAYPOINT_HOST`
- YAML values from `waypoint.yaml`
- built-in defaults

Keep env vars for machine-specific overrides and secrets. Prefer the YAML file for structured settings and grouped config such as remote Codex SSH options.

## Optional remote Codex over SSH

Waypoint can route managed `codex` sessions through SSH instead of starting a local Codex app-server.

The current config shape supports one optional `codex_remote` profile with:

- top-level backend defaults such as `host`, `port`, `password`, and `data_dir`
- `ssh_destination` and optional `ssh_args`
- `codex_bin` on the remote host
- `remote_env` for secrets such as `OPENAI_API_KEY`
- `cwd_mappings` to translate local launch paths into remote workspace paths
