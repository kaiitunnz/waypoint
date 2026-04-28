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

Prefer the YAML file for structured settings and grouped config such as named SSH coding targets. Use env vars only for machine-specific overrides where editing the YAML is awkward.

## Optional SSH coding targets

Waypoint can expose named SSH coding targets alongside its local launch path. The frontend backend picker keeps host URLs and SSH targets at the same level; choosing an SSH target only changes how new managed sessions launch on the currently selected Waypoint host.

The current config shape supports zero or more `ssh_targets` entries with:

- `default_backend` and `default_cwd`, which seed the frontend launch form
- top-level backend defaults such as `host`, `port`, `password`, and `data_dir`
- `id` and `name` for picker/UI identity
- `ssh_destination` and optional `ssh_args`
- `supported_backends`, which defaults to both `codex` and `claude_code`
- `codex_bin` and `claude_bin` on the remote host
- `default_remote_cwd`, which seeds the remote working-directory field and defaults to `~`
- `remote_env` for secrets such as `OPENAI_API_KEY`

Managed launches always keep a local `cwd` for repo metadata and UI display. When an SSH target is selected, the frontend also submits an explicit `remote_cwd` for the remote host; if it omits one, Waypoint uses that target's `default_remote_cwd`. Remote `codex` launches use the Codex app-server over SSH, while remote `claude_code` launches use tmux plus an SSH-wrapped `claude` command.
