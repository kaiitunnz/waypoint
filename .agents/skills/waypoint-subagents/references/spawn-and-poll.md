# Spawn and Poll

## Spawn a child

```bash
waypoint sessions start \
  --backend <id> \
  --cwd <path> \
  --title "subagent:<short-purpose>" \
  [--model <model>] [--effort <effort>] [--permission-mode <mode>]
```

- Pick `--backend` deliberately — this is the main reason to use a Waypoint
  sub-session over a harness subagent. Use `waypoint backends` to see which
  backend ids are registered, and `waypoint doctor` to check local CLI
  availability when launch fails.
- Pick `--cwd` deliberately. For repo work, pass the repo root; for scratch or
  host inspection, pass an explicit safe directory. Do not assume the child
  should inherit your own working directory.
- Always set the `subagent:` title prefix so the session is recognizable as one
  you own.
- A child on the **same backend** inherits your permission mode automatically;
  cross-backend children fall back to that backend's default. Pass
  `--permission-mode` only to override this. See `references/permissions.md`.
- Capture the returned session id. Keep it for the rest of the turn.

Then send the task as the first input:

```bash
waypoint sessions send <session-id> "<task instructions>"
```

## Poll to completion

There is no blocking `wait` command, so poll `sessions show` until the child
reaches a terminal state. Terminal/idle statuses to stop on:

- `idle` / `waiting_input` — the child has finished its turn (possibly awaiting
  more input or an approval).
- `exited` — the child process ended.
- `error` — the child failed; read its events to find out why.

Statuses that mean keep waiting: `starting`, `running`, `interrupted`.

The CLI always prints JSON; `sessions show <id>` emits `{"session": {...}}`, so
the status lives at `.session.status`. A reasonable poll loop with an interval
and a hard timeout:

```bash
sid=<session-id>
deadline=$((SECONDS + 600))   # cap the wait; tune per task
while :; do
  status=$(waypoint sessions show "$sid" | jq -r .session.status)
  case "$status" in
    idle|waiting_input|exited|error) break ;;
  esac
  [ "$SECONDS" -ge "$deadline" ] && { echo "timed out waiting on $sid"; break; }
  sleep 5
done
```

Parse the JSON `status` field rather than scraping human output. The status
values are lowercase: `starting`, `idle`, `waiting_input`, `running`,
`interrupted`, `exited`, `error`.

## Fan-out

To run several children in parallel, start them all, collect their ids, then
poll each (or loop over the set each tick). Keep the count small and
intentional — there is no server-side cap, so an unbounded loop will exhaust the
host.
