# The event wake

The manager is driven by events, not polling. `waypoint sessions wake-on-board`
registers a subscription; the runtime then **starts a turn** on the subscribed
session whenever a matching board channel or the inbox changes. This is the same
input path the scheduler already uses, so it is backend-agnostic — the manager and
its leads work identically as Claude Code, Codex, or OpenCode sessions.

## Register the manager's wake (at setup)

The manager subscribes to its intake channel, all per-ticket channels, and answers
to its own inbox items:

```bash
waypoint sessions wake-on-board "$WAYPOINT_SESSION_ID" \
  --channels {{tickets_channel}} \
  --channels '{{ticket_channel_prefix}}*' \
  --wake-on-inbox
```

- `--channels <glob>` is an **fnmatch** glob, repeatable. `{{tickets_channel}}`
  catches new and changed tickets; `{{ticket_channel_prefix}}*` catches every lead's
  status/relay posts across all per-ticket channels in one subscription.
- `--wake-on-inbox` wakes on any **non-self** inbox mutation — i.e. a human
  answering one of the manager's own items. This is how the substantial-spec gate
  and the per-PR review answers reach the manager.
- `--kinds <k>` is stored on the subscription for reference only; it is **not
  enforced** by the content-free wake, so do not rely on it to filter. The wake
  fires on any matching-channel post regardless of `kind`; the manager
  distinguishes signals by re-reading the `status` cell's `kind=` meta.

Remove subscriptions with `waypoint sessions wake-off <sid> --all` (or `--id
<sub-id>` for one). Subscriptions live in the database and survive backend
restarts — no re-registration needed on boot; the manager just re-drains on its
next wake.

## Each lead subscribes to its own ticket channel

When the manager spawns a tech-lead, it registers that lead's wake on the lead's
own channel so relays wake it:

```bash
waypoint sessions wake-on-board <lead-sid> --channels {{ticket_channel}} --wake-on-inbox
```

This is what makes the durable relay (`references/loop.md`) wake the lead the
moment the manager posts it; the content-free `sessions send` nudge is only a
fallback.

## The wake is content-free — always re-read

A wake carries **no payload**. It means only "a channel or the inbox you watch
changed — re-read and reconcile." Never treat the fact of a wake, or its timing,
as information. On every wake the manager re-reads `manager next`, the board cells,
and the relay logs and rebuilds its position (`references/loop.md`). A burst of
posts may coalesce into one wake, and two posts may double-fire — both are benign
because the payload is always re-read and the drain is idempotent.

## Self-exclusion — never wake yourself into a livelock

The manager writes the very channels it subscribes to (`ticket-<id>` status/relay
posts, `org` summaries) and files the very inbox items it later reads answers to.
The runtime **excludes the mutating author** from its own wake on both axes, so
these self-writes do not livelock the manager. This holds only if every board and
inbox write is authored as the manager:

- Board posts default `--author-session-id` from `$WAYPOINT_SESSION_ID` — keep it
  so, and never post to a watched channel under a different author id.
- Inbox answers default `--actor-session-id` from `$WAYPOINT_SESSION_ID`. A human
  answer carries **no** session, so it *does* wake the manager (correct — that is
  the signal); the manager reading answers via `inbox get` (a plain GET) triggers
  no broadcast at all.

The manager only *creates* inbox items (self-excluded) and reads answers via the
non-mutating `inbox get`, so it rarely self-mutates the inbox — but the exclusion
is what makes it safe regardless.

## State-aware delivery (why a wake sometimes waits)

The runtime delivers a wake only into a state that can accept input, and defers
otherwise — so a wake never lands mid-turn or resurrects a stopped session:

- **`idle` / `waiting_input`-final** → the turn starts now.
- **`running` / `starting` / `interrupted` / `waiting_input`-awaiting-approval** →
  the wake is marked pending and fires on the next transition into a deliverable
  state (e.g. `running → idle` at turn end). It never interrupts an active turn or
  injects while an approval is pending.
- **`exited` / `error`** → **not woken** — a stopped session is never resurrected
  by a board post; only reconcile (via the liveness check) or an explicit resume
  recovers it.

This means an in-flight manager turn absorbs new posts and drains them at its own
turn boundary, and a manager that has genuinely stopped stays stopped until a
human or a lead-liveness reconcile brings it back — both correct.

## `board wait` is not the loop driver

`waypoint board wait --channels <glob>… [--since <id>] [--timeout <dur>]` blocks
until a watched channel gets a new entry and emits `{"outcome", "channel",
"entries"}` (`outcome` is `changed` or `timeout`; exit `0` on changed, `124` on
timeout). It is an **interactive convenience** for a human or a one-off script
waiting on the board — explicitly *not* the manager's loop driver. The manager is
driven by the registered `wake-on-board` subscription and its own drain, never by
sitting in a blocking `board wait`.
