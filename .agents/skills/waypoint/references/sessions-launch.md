# Launching Sessions

Launch through the running server:

```bash
waypoint sessions start --backend <id> --cwd <path>
waypoint sessions start --backend <id> --cwd <path> --title <title>
waypoint sessions start --backend <id> --cwd <path> --model <model>
waypoint sessions start --backend <id> --cwd <path> --effort <effort>
waypoint sessions start --backend <id> --cwd <path> --permission-mode <mode>
```

Use `waypoint backends` to discover registered backend ids and capabilities.
Use `waypoint models [backend]` to confirm the model ids and reasoning efforts a
backend actually offers before passing `--model` / `--effort` — pass those ids
verbatim rather than guessing. Use `waypoint doctor` for local CLI availability
when launch fails, and `waypoint sessions start --help` for the installed flag
surface.

Choose `cwd` deliberately. For repository work, use the repo root. For host
inspection or scratch work, use an explicit safe directory rather than assuming
the assistant workspace is the user's target project.
