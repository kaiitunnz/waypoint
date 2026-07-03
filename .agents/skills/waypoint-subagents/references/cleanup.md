# Cleanup

Reap the children you spawned once you are done with them. A child left running
idle because you may still **iterate** on it is fine — that is *parking*, covered
below. What is a bug is an **orphaned** child: one you have finished with (or
forgotten), left running or as a stale `exited` record, cluttering the user's
session list.

There are two operations:

- `waypoint sessions terminate <id>` — stops a running child; the record
  remains (status `exited`), so the user can still review it.
- `waypoint sessions delete <id>` — terminates if needed, then **removes the
  record** entirely. Add `--force` only for a wedged adapter that won't
  terminate gracefully.

## Disposition: delete by default, keep deliberately

When you are done with a child, decide its fate. **Default to deleting it** —
most children do internal, throwaway work whose result you have already consumed
or relayed, and a left-behind record is just clutter.

```bash
waypoint sessions delete <child-id>
```

**Keep** a child when:

- **You may still iterate on it.** If the work is likely to need another turn —
  a follow-up, a fix, a re-run against changed inputs — **park it, don't reap it**:
  leave it idle so you can `waypoint sessions send <id> "<next instruction>"` and
  continue in place. Reaping is a one-way door — to pick the work back up you would
  have to **reimport the thread**, which spins a *new* session, replays the history
  into it, and loses the live session state. Bound how many you keep parked (an
  idle child still holds resources — see below), and reap it once you are genuinely
  done or it has gone stale.
- It produced output the **user should review**.
- It ended in `error` — deleting it would hide the failure. Leave it and surface
  it to the user.
- It is **pinned** by the user (`pinned_at` set) — never delete a pinned session.

When you keep children, tell the user which ones and why, and quote their ids so
they can find them.

**Park ≠ terminate.** Parking means leave the child **idle and alive** — neither
`terminate` nor `delete`. `terminate` ends the process, and whether you can pick
the child back up then depends on its **transport's** `supports_resume`: a
non-resumable transport (`supports_resume=False` — the structured `claude_cli`
adapter, and `codex` / `opencode`) **cannot** be resumed, so continuing it forces
the very reimport parking avoids; `claude_tty` / `tmux` (`supports_resume=True`)
re-attach after the process ends — and a default `claude_code` subagent runs on
`claude_tty`, so it *is* resumable. Parking (leave it running idle) is safe on
every transport; `terminate` is recoverable only on the resumable ones, so don't
use it as a "lighter" form of keeping.

## Rules

- Delete or terminate **only** the session ids you spawned this turn and tracked.
  Never touch a session you cannot positively account for as your own; when in
  doubt, leave it and ask the user.
- The personal-assistant session cannot be terminated or deleted — the server
  rejects it with `403`. Never target it.
- If a child has already `exited` and is not worth keeping, `delete` it to clear
  the record; `terminate` alone would leave the clutter behind.
- Throwaway children — tests, quick one-shot fan-out — should always be deleted.
