# Lifecycle Commands

Targets are `backend`, `frontend`, `frontend-preview`, or `all`:

```bash
waypointctl start [backend|frontend|frontend-preview|all]
waypointctl stop [backend|frontend|frontend-preview|all]
waypointctl restart [backend|frontend|frontend-preview|all]
waypointctl status
```

`frontend` covers the optional preview too; `frontend-preview` addresses only the
preview. A failing preview is reported (exit 1) without stopping the rest. A lone
preview start or restart is refused when it would disturb the running primary;
the error names the command to run.

Starting is generally safe. Frontend restart is safe for frontend-only changes.
Backend or full-stack restart requires confirmation because it interrupts live
sessions.

Use `waypointctl restart frontend` after frontend code changes. For backend
changes, follow `restart-safety.md`.

**`start` and `restart` are non-blocking.** They kick off the work and return
immediately, before services are healthy — the frontend in particular runs a
production build on start that takes seconds. A `status` run right afterwards
showing a service `stopped` or `starting` is the build in progress, **not** a
failure, and does not need a manual `start` to recover. Poll until healthy
rather than concluding from one early check:

```bash
for _ in $(seq 1 30); do
  waypointctl status | grep -q 'health=healthy' && break
  sleep 2
done
waypointctl status
```

(Do not poll with `waypointctl logs` — it blocks; see `observe.md`.)

**`restart` deploys whatever branch and working tree are checked out** — the
process loads source at startup, so the running stack reflects the on-disk
checkout, not `main`. Running the stack on a feature branch that is behind
`main` silently drops fixes already merged there. Before a backend/full-stack
restart, confirm the checkout is current (e.g. `git rev-list --left-right
--count main...HEAD`); fast-forward or rebase a stale branch onto `main` first.
