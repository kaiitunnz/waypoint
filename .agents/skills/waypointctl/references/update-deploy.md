# Update And Deploy

The repository is usually at `$WAYPOINT_HOME`.

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
