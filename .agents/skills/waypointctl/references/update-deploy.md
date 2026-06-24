# Update And Deploy

The repository is usually at `$WAYPOINT_HOME`.

## Preferred: `waypointctl update`

```bash
waypointctl update                 # latest release tag
waypointctl update --ref vX.Y.Z    # a specific tag
waypointctl update --nightly       # tip of main
```

`update` refuses to run if the checkout has uncommitted changes, then fetches,
checks out the target (release tags detach; branches like `main` track the
remote tip), reinstalls `waypointctl` via `uv tool install --force`, and
restarts the stack with a forced frontend rebuild. Because it restarts, treat it
like a restart: get the user's permission first and follow `restart-safety.md`.

## Manual flow (when you need step-by-step control)

Before pulling:

```bash
git -C "$WAYPOINT_HOME" status
git -C "$WAYPOINT_HOME" rev-parse --abbrev-ref HEAD
```

If the tree is dirty or not on `main`, stop and tell the user. Otherwise:

```bash
git -C "$WAYPOINT_HOME" pull --ff-only origin main
```

If backend dependencies changed:

```bash
cd "$WAYPOINT_HOME/backend" && uv sync
```

If `waypointctl/` changed, reinstall from source:

```bash
uv tool install "$WAYPOINT_HOME/waypointctl" --reinstall
```

Then restart only what changed, following `restart-safety.md` for backend or
full-stack restarts.
