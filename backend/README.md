# Waypoint Backend

FastAPI daemon for managing and observing Claude Code and Codex sessions.

Managed Codex sessions use the local Codex App Server SDK from `../3rdparty/codex/sdk/python`. Claude and attached sessions still use tmux.

## Development

```bash
git submodule update --init --recursive
cp waypoint.example.yaml waypoint.yaml
# edit waypoint.yaml and set a real password
uv sync --group dev
uv run waypoint serve
```

## Configuration

Waypoint reads a single YAML config file. By default it looks at `backend/waypoint.yaml` (silently treated as empty if missing); override the location with `--config <path>` or `WAYPOINT_CONFIG_PATH=<path>`.

Precedence:

- CLI flags such as `--host`, `--port`, and `--config`
- environment variables such as `WAYPOINT_HOST` (handy for systemd/launchd units)
- YAML values from `waypoint.yaml`
- built-in defaults

Prefer the YAML file for structured settings and grouped config such as remote Codex SSH options. Use env vars only for machine-specific overrides where editing the YAML is awkward.

## Optional remote Codex over SSH

Waypoint can route managed `codex` sessions through SSH instead of starting a local Codex app-server.

The current config shape supports one optional `codex_remote` profile with:

- `default_backend` and `default_cwd`, which seed the frontend launch form
- top-level backend defaults such as `host`, `port`, `password`, and `data_dir`
- `ssh_destination` and optional `ssh_args`
- `codex_bin` on the remote host
- `default_remote_cwd`, which seeds the frontend launch form and defaults to `~`
- `remote_env` for secrets such as `OPENAI_API_KEY`

Managed Codex launches always keep a local `cwd` for repo metadata and UI display. The frontend also submits an explicit `remote_cwd` for the remote host; if it omits one, Waypoint uses `default_remote_cwd`.
