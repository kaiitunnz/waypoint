# Lifecycle Commands

Targets are `backend`, `frontend`, or `all`:

```bash
waypointctl start [backend|frontend|all]
waypointctl stop [backend|frontend|all]
waypointctl restart [backend|frontend|all]
waypointctl status
```

Starting is generally safe. Frontend restart is safe for frontend-only changes.
Backend or full-stack restart requires confirmation because it interrupts live
sessions.

Use `waypointctl restart frontend` after frontend code changes. For backend
changes, follow `restart-safety.md`.
