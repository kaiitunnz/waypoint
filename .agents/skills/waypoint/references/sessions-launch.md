# Launching Sessions

Launch through the running server:

```bash
waypoint sessions start --backend <id> --cwd <path>
waypoint sessions start --backend <id> --cwd <path> --title <title>
waypoint sessions start --backend <id> --cwd <path> --model <model>
waypoint sessions start --backend <id> --cwd <path> --effort <effort>
waypoint sessions start --backend <id> --cwd <path> --permission-mode <mode>
```

Use `waypoint doctor` or `waypoint sessions start --help` to discover available
backend ids and local CLI availability when launch fails.

Choose `cwd` deliberately. For repository work, use the repo root. For host
inspection or scratch work, use an explicit safe directory rather than assuming
the assistant workspace is the user's target project.
