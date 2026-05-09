# Waypoint Backend

FastAPI daemon for managing and observing Claude Code, Codex, and OpenCode sessions.

Managed Codex sessions use the local Codex App Server SDK from `../3rdparty/codex/sdk/python`. Claude and OpenCode sessions use their native APIs.

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

A config may define zero or more `ssh_targets` entries with:

- `default_backend` and `default_cwd`, which seed the frontend launch form
- top-level backend defaults such as `host`, `port`, `password`, and `data_dir`
- `id` and `name` for picker/UI identity
- `ssh_destination` and optional `ssh_args`
- `plugin_configs`, a plugin-id → per-target plugin config mapping. Presence of a key means "this target supports the plugin"; omitting `plugin_configs` entirely defaults to every registered non-fallback plugin (`codex`, `claude_code`, and `opencode`). Each block is validated against the plugin's `launch_target_schema` — common fields include `remote_bin` (path to the CLI on the remote host; falls back to the plugin's `cli_binary`), and codex adds `config_overrides` for the `--config K=V` flag.
- `default_cwd`, which seeds the remote working-directory field and defaults to `~`
- `remote_env` for secrets such as `OPENAI_API_KEY`

Managed launches use a single `cwd` value for both UI display and the actual remote working directory. When an SSH target is selected, the frontend seeds that field from the target's `default_cwd`. Remote `codex` launches use the Codex app-server over SSH, remote `claude_code` launches use tmux plus an SSH-wrapped `claude` command, and remote `opencode` launches use `opencode serve` over SSH.

## Remote Claude thread import

Claude Code has no `thread/list` RPC, so for both local and remote SSH targets Waypoint discovers resumable sessions by reading the JSONL transcripts the CLI persists under `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/`. Remote targets run a small read-only bash + jq helper (`backend/scripts/claude_thread_enumerator.sh`) over the existing `bash -ilc` SSH wrapper via `bash -s`; the script body is fed on subprocess stdin so nothing lands on disk on the remote. Listings are cached per target for 30 s and invalidated on import or session delete.

Remote prerequisites:

- `bash`, `jq`, and `perl` on the remote host. If any are missing, the list call collapses to an empty result with a rate-limited WARN log on the backend; the UI shows the same "No importable Claude threads found." hint as the no-threads case.
- The remote login shell must keep stdout silent — any rcfile that writes to stdout will corrupt other remote launches too. The enumerator emits a `__WP_BEGIN__` sentinel so its own parser can recover from rcfile noise, but the safer fix is to send rcfile output to stderr.
- For sub-second list latency on cache miss, recommend SSH connection multiplexing in `waypoint.yaml`:

  ```yaml
  ssh_targets:
    - id: devbox
      ssh_destination: dev@example.com
      ssh_args:
        - -o
        - ControlMaster=auto
        - -o
        - ControlPath=~/.ssh/cm-%C
        - -o
        - ControlPersist=600s
  ```

  Without multiplexing, a fresh SSH connection per list call dominates latency (~1–2 s); with `ControlPersist`, subsequent calls reuse the existing master connection and complete in ~100 ms.
