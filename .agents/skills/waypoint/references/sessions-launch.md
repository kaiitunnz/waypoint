# Launching Sessions

Launch through the running server:

```bash
waypoint sessions start --backend <agent-id> --cwd <path>
waypoint sessions start --backend <agent-id> --cwd <path> --title <title>
waypoint sessions start --backend <agent-id> --cwd <path> --model <model>
waypoint sessions start --backend <agent-id> --cwd <path> --effort <effort>
waypoint sessions start --backend <agent-id> --cwd <path> --permission-mode <mode>
waypoint sessions start --backend <agent-id> --cwd <path> --transport <transport-id>
```

`--backend` selects the **agent** (`claude_code`, `codex`, `opencode`);
`--transport` is a secondary axis that picks the interface — Chat
(`claude_cli`), Emulated (`claude_tty`), or Terminal (`tmux`). `claude_tty` and
`tmux` are transports, not agents: drive the Emulated TUI with `--backend
claude_code --transport claude_tty`, not `--backend claude_tty` (a legacy
alias). Omitting `--transport` uses the agent's default transport, which for a
local `claude_code` launch is Emulated (`claude_tty`).

Use `waypoint backends` to discover registered agent ids, their
`supported_transports` / `default_transport`, and capabilities. Use `waypoint
models [backend]` to confirm the model ids and reasoning efforts a backend
actually offers before passing `--model` / `--effort` — pass those ids verbatim
rather than guessing. Use `waypoint doctor` for local CLI availability when
launch fails, and `waypoint sessions start --help` for the installed flag
surface.

Choose `cwd` deliberately. For repository work, use the repo root. For host
inspection or scratch work, use an explicit safe directory rather than assuming
the assistant workspace is the user's target project.

## Presets

A **preset** is a reusable, server-side bundle of launch defaults — backend,
transport, model, effort, permission mode, launch target, launch_mode, args,
config_overrides, launch_env, and tags. It deliberately excludes `cwd` and
`title`: those are per-launch specifics, not reusable defaults, so you always
supply them at launch. A preset is resolved at launch/schedule time; any
explicit flag you pass **always overrides** the preset's value. Prefer a
user-provided or default preset for repeated worker roles instead of re-deriving
model/permission/transport from scratch each time.

```bash
waypoint presets list                          # all presets (env values redacted) + the default id
waypoint presets show <id-or-name>             # one preset; env values redacted
waypoint presets show <id-or-name> --show-secrets   # reveal launch_env values
waypoint presets create --name worker-codex-high \
  --backend codex --model <model> --effort high \
  --permission-mode <auto-approving-mode> \
  --launch-env KEY=VAL --config-override X --tag role=worker [--default] [ARGS...]
waypoint presets update <id-or-name> [same launch options]   # PATCH: only passed fields change
waypoint presets delete <id-or-name>           # existing sessions/schedules are unaffected
waypoint presets default [<id-or-name>]        # set the default, or print it with no arg
waypoint presets clear-default
```

`update` has PATCH semantics — omitted fields (including `--tag`) are preserved,
so pass only what you want to change. Deleting a preset does not touch sessions
or schedules already launched from it.

Launch from a preset with `--preset`:

```bash
waypoint sessions start --preset worker-codex-high --cwd <path>  # backend etc. from preset; cwd always explicit
waypoint sessions start --preset worker-codex-high --cwd <path> --model <id>  # explicit flag overrides the preset
waypoint sessions start --no-preset --backend <id> --cwd <path>  # ignore the default preset
```

With `--preset`, `--backend` becomes optional when the preset (or the default)
supplies it. `--cwd` is never a preset field, so it stays required at launch —
an omitted cwd fails with a clear 400. When `--preset` is omitted and a default
preset exists, it is applied automatically — pass `--no-preset` to opt out. Run
`waypoint presets list` before choosing settings from scratch, and still consult
`waypoint backends` / `waypoint models` when overriding or creating a preset so
the ids you pin are real.
