# Scheduling Launches and Messages

Two deferred-action command groups run against a live server. `waypoint
schedule ...` launches a **session** later; `waypoint schedule message ...`
sends a **message** to an existing session later. Both persist across restarts
and fire from the server's scheduler, not the CLI process — the shell that
created them can exit.

## Timing (both groups)

Every schedule needs exactly one of:

- `--scheduled-at <iso8601>` — absolute time; must be in the future. A datetime
  without a timezone is read as UTC.
- `--delay-seconds <n>` — relative to now; must be non-negative.

If both are given, `--scheduled-at` wins. Omitting both is an error.

## Scheduled sessions

```bash
waypoint schedule list
waypoint schedule create --backend <agent-id> --cwd <path> --delay-seconds 300
waypoint schedule create --backend <agent-id> --cwd <path> --scheduled-at 2026-07-02T09:00:00Z --prompt "start the migration"
waypoint schedule delete <schedule-id>
waypoint schedule clear-history          # drop launched/cancelled records
```

`schedule create` takes the same launch options as `waypoint sessions start`
(`--backend`, `--cwd`, `--transport`, `--title`, `--model`, `--effort`,
`--permission-mode`, `--prompt`, and trailing `args`); see
`references/sessions-launch.md` for what they mean and how to pick a
transport. A record moves `pending → launched | cancelled | failed`; once
launched it carries the new `session_id`, and a `failure_reason` on failure.

## Scheduled messages

```bash
waypoint schedule message list
waypoint schedule message list --session-id <session-id>
waypoint schedule message create <session-id> "ping the reviewer" --delay-seconds 600
waypoint schedule message create <session-id> "run the suite" --scheduled-at 2026-07-02T09:00:00Z
waypoint schedule message create <session-id> "draft only" --no-submit
waypoint schedule message delete <schedule-id>
waypoint schedule message clear-history [--session-id <session-id>]
```

The target session must already exist. `--no-submit` queues the text into the
session's input without auto-submitting it (the default submits). A record moves
`pending → sent | cancelled | failed`.

## Notes

- Prefer JSON output and read back the assigned schedule id from the response
  rather than assuming it.
- `clear-history` only removes terminal records (launched/sent/cancelled/
  failed); it never cancels a pending one — use `delete` for that.
- `waypoint reset` wipes schedules along with other runtime data (see
  `references/destructive-ops.md`).
- Run `waypoint help` for the exact installed flag surface rather than trusting
  the lists here.
