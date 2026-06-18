# Restart Safety

Before backend or full-stack restart:

1. Run `waypoint sessions list`.
2. Surface sessions that are `running` or `waiting_input`.
3. Confirm the checked-out branch is current so the restart deploys the code you
   expect (`restart` deploys the on-disk checkout, not `main` — see
   `lifecycle.md`).
4. Ask the user to confirm interruption — **every time**. Permission for one
   restart does not carry over to a later one in the same session; each restart
   is separately disruptive, so re-ask (a yes/no question) before each.
5. Prefer daemon-backed restart so the caller is not killed mid-command.

Commands:

```bash
waypointctl daemon start
waypointctl restart backend
waypointctl restart all
```

Avoid `--wait` from inside a hosted backend process unless the user understands
the caller may be interrupted. Restart is non-blocking, so after it returns,
verify by polling status until healthy (do **not** use `waypointctl logs` here —
it blocks like `tail -f`; see `observe.md`):

```bash
for _ in $(seq 1 30); do
  waypointctl status | grep -q 'health=healthy' && break
  sleep 2
done
waypointctl status
```
