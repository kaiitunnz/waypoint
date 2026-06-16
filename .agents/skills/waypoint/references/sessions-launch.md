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
