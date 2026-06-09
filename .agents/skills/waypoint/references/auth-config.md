# Auth And Config

The `waypoint sessions` CLI talks to the running server over HTTP. On the same
host, `waypoint serve` writes a token under the backend data dir so the CLI can
authenticate without prompting for the password.

Useful diagnostics:

```bash
waypoint doctor
waypoint --config <path> doctor
```

Use `--config <path>` when the user points at a non-default `waypoint.yaml`.
Environment overrides may affect data dir, host, port, and password.

If the server is unreachable, inspect the stack with the `waypointctl` skill
rather than guessing.
