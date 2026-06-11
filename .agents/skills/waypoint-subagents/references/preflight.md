# Preflight

Run this once before treating Waypoint sub-sessions as available. It is cheap
and prevents acting on a CLI that cannot reach the server.

## 1. Is the CLI on PATH?

```bash
waypoint --help
```

If `waypoint` is not found, the server was likely started without its virtualenv
on `PATH`. Try the backend venv directly before giving up:

```bash
"$WAYPOINT_HOME/backend/.venv/bin/waypoint" --help   # if WAYPOINT_HOME is set
```

If neither resolves, stop: report that the `waypoint` CLI is unavailable in this
session and that subagent sessions cannot be managed from here.

## 2. Is it authenticated and pointed at the running server?

```bash
waypoint doctor
waypoint sessions list
```

`sessions list` returning the current session set confirms both connectivity and
auth. Authentication resolves automatically in this order, so no setup is
normally needed when you run as the same user as the server:

1. `WAYPOINT_TOKEN` environment variable.
2. The token file at `~/.waypoint/backend-data/cli-token`.
3. Password login via `WAYPOINT_PASSWORD` or the server config.

If `sessions list` fails with an auth error, report it — do not attempt to
discover or write credentials yourself.

## Notes

- The CLI computes the server URL from local settings and connects over
  loopback; you are talking to the same server you run under.
- Prefer JSON output and concrete session ids over inferring state from memory.
