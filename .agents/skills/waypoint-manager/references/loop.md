# The manager loop

The manager's correctness rests on one idea: **crash-safe convergence through
write-ahead intent and idempotent reconcile**. Procedure state lives outside the
context window (the `waypoint manager` machine + the board), every wake rebuilds
position from that state, and every side effect is guarded so a mid-turn crash or
a growing context **resumes** the procedure instead of repeating it. This file is
the exact cycle; `references/state-machine.md` is the transition authority and
`templates/manager/loop-cycle.md` is the prompt the manager re-reads each wake.

## What drives a turn

Nothing polls. The manager registers an event wake (`references/wake.md`) on the
`tickets` channel, all `ticket-*` channels, and its own inbox answers, and the
runtime starts a turn whenever one changes. A slow liveness timer is the *only*
self-scheduled wake — a minutes-scale backstop for duties with no event source
(dead/parked-lead detection, expired-lease steal, latency-timeout abandonment,
and `gh`/CI advancement). It is a backstop, not the correctness mechanism; the
drain is correct even if the timer never fires.

## Surviving a backend restart

The manager runs on `claude_tty` (claude_code's default transport), whose agent
process lives under a persistent pty, so an in-flight turn keeps executing even
while the Waypoint backend is restarting; boot-restore reattaches the session and
re-reads the wake subscriptions from the DB. Two consequences for the loop:

- **A `waypoint` CLI call that fails with a connection error is transient**, not a
  failure of the work — the backend is down mid-restart. Retry it with backoff
  until the backend answers; **never** count it against a ticket's `attempts` or
  `lead_restarts` budget or move the ticket to `blocked` over it. Only a real
  agent/spawn failure — the backend answered and the operation itself failed —
  consumes a budget.
- **Events that arrive while the backend is down do not wake the manager** (the
  wake driver is the backend, and the pending-wake set is in-memory). They are not
  lost — they sit durably on the board — but an idle manager only picks them up on
  its next wake, so the slow liveness timer is the catch-up that guarantees
  progress after a restart. If the manager is still mid-turn when the backend
  returns, it simply keeps draining and re-reads the board itself.

## Drain to a fixpoint, not one action per wake

Every wake **drains all currently-actionable work to a fixpoint**, then idles.
This is what lets a slot-freeing action (a merge, an abandon) enable the next
action *in the same turn* without a self-wake. Maintain a per-drain `tried` set
(ticket ids that failed an action this drain) and repeat:

1. **Re-anchor.** `waypoint manager next --json` (passing `--tried <id>` for each
   id already in the drain's `tried` set) → derived slots, per-ticket legal
   transitions, and the one recommended action. Re-read
   `templates/manager/loop-cycle.md` so the procedure is re-injected, not
   remembered. If there is no recommendation **and** no outstanding external
   signal (below), the drain is done — go idle.
2. **Reconcile — adopt reality before acting.** See below.
3. **Choose** the single action: the `recommended` pull move if present, or an
   observed external edge reconcile surfaced (spec posted, human answer, done/
   partial, human merge, dead lead, merged PR). Highest-priority first.
4. **Record intent** with a dedup key *before* the side effect (next section).
5. **Act idempotently** (the section after).
6. **Confirm** — write the resulting ids back onto the ticket
   (`--lead-session-id`, `--pr-url`); on a failed delegate, add the ticket to
   `tried` and continue. Loop to step 1.

**Termination is guaranteed.** Each iteration strictly advances a well-founded
measure: an action either moves a ticket toward a terminal or awaiting-human
state, occupies a bounded slot, or (on failure) is added to `tried` and not
re-selected this drain. Auto-retry is bounded by the per-ticket `attempts` budget
with backoff; past the budget the ticket goes to `blocked`-awaiting-human, never
an unbounded spin. So the drain always reaches a fixpoint.

## Reconcile: adopt reality before acting

Recorded state is a hypothesis; observed reality wins. Before choosing an action,
each iteration:

- **Re-read the board explicitly.** Read the `tickets` channel and every in-flight
  `ticket-<id>` `status` cell *by key* — not via `--since`, because a keyed cell
  overwrite keeps its row id and a `--since` poller misses it (`board read
  ticket-<id> --key status`). Read the relay logs (append posts) with `--since`.
- **Match spawned sessions to tickets.** `waypoint sessions list --spawned-by
  $WAYPOINT_SESSION_ID --recursive` and match `subagent:ticket-<id>:<role>` titles:
  - a **live** child whose ticket is not recorded as spawned → **adopt** it (record
    its `--lead-session-id`); do not re-spawn.
  - a **dead** child (`sessions show` → `exited`/`error`) in any live-lead state
    (`delegated`, `building`, `revising`, `blocked`, `review_requested`,
    `spec_pending`) → drive the **lead-died self-loop** (`references/git-integration.md`).
- **Check PR/CI reality.** For a ticket in `review_requested`/`merging`, `gh pr
  view <pr-url> --json state,mergeStateStatus,statusCheckRollup`: an
  already-`MERGED` PR means the merge happened (record it, do not re-merge); CI
  state gates the merge action.
- **Check lead liveness in *every* live-lead state**, including parked leads in
  `blocked`/`review_requested` — a backend restart can mark a reattach-failed lead
  `error` while parked, and only reconcile catches it.

Reconcile is why a crash never duplicates: a re-fired spawn finds the live orphan
and adopts it; a re-attempted merge finds the PR already merged and stops.

## Write-ahead intent + dedup key

Record intent **before** the observable side effect, so a crash between "intend"
and "do" is recoverable by reconcile:

- **Delegate.** `ticket transition <id> --to delegated --intended-lead-title
  "subagent:ticket-<id>:tech-lead" --branch ticket/<id> --worktree-path <sibling>`
  *first* (this bumps `attempts` and reserves the unique title — the invariant
  rejects a second ticket claiming the same title), *then* spawn. If the turn dies
  after the transition but before the spawn, the next drain sees a `delegated`
  ticket with no live lead and re-spawns into the reserved title — exactly once.
- **Merge.** Acquire the lease and `ticket transition <id> --to merging --pr-url
  <url>` before touching `gh pr merge`; reconcile against `gh pr view` on the next
  drain if the turn died mid-merge.
- **Relay.** Append the relay post stamped `relay_version` before the nudge (below).

The `intended_lead_title` uniqueness invariant and the single-`merging` invariant
are the server-side halves of this: even a confused context cannot double-book a
title or run two merges.

## Act idempotently

- **Spawn** only if no **live** session with the intended title exists (reconcile
  already checked). An initial delegate whose worktree create collides with a
  stale `ticket/<id>` branch from an incomplete reap has no committed work — delete
  that branch and re-create. A lead-died resume has work on the branch — terminate
  (never delete) the dead session and spawn onto the preserved worktree with
  `--cwd <worktree_path>`. Full mechanics: `references/git-integration.md`.
- **Relay** via the durable versioned log (next section), never a bare
  `sessions send` carrying the payload.
- **Merge** with `gh pr merge` only if `gh pr view` does not already report
  `MERGED`.

## The durable versioned relay log

A `waypoint sessions send` is a *fire-and-forget, non-observable* sink — it starts
a turn but leaves no durable record, so a lead that dies mid-processing loses the
message. Every manager→lead relay (a human answer to a blocker, review feedback, a
re-delegate briefing) is therefore an **append-log post to `ticket-<id>`** stamped
with the source version, followed by a **content-free** nudge:

```bash
# 1. Durable payload on the log, versioned by the inbox answer's version:
waypoint board post ticket-<id> "<the human answer / review feedback>" \
  --meta relay_version=<inbox answer version> --meta kind=relay
# 2. Content-free nudge — carries NO authoritative payload, just wakes the lead:
waypoint sessions send <lead-sid> "[wp-msg from=<manager-sid>] Relay posted on ticket-<id>; read owed relays and act."
```

The lead is **wake-subscribed to its own `ticket-<id>` channel**, so the post
wakes it via the state-aware path; the `sessions send` is a belt-and-suspenders
fallback, retried on the manager's next reconcile if the lead is still behind.

This makes relays:

- **complete** — re-derivable from the log on every lead (re)entry, so a fresh
  lead after a death still sees every owed relay;
- **idempotent** — the lead consumes posts where `relay_version >` the highest it
  has processed, acts once, and records that version; a duplicate nudge or re-send
  changes nothing;
- **death-surviving** — the payload is on the durable board, not in a lost send.

So a human answer is **delivered at-least-once and applied at-most-once**. The
manager's own `last_relayed_version` is only a hint; the `ticket-<id>` log is
authoritative (and it is not settable through the CLI — rely on the log). The
consumer half lives in the tech-lead templates: on every entry and nudge, read
owed relays and record the highest version acted on.

## Why context growth cannot derail it

State is rebuilt from the machine + board each iteration; the legal next actions
come from `manager next`, not the transcript; every side effect is intent-guarded,
reconcile-adopted, or version-consumed. A growing or compacted context window
changes what the manager *remembers* but not what it *can legally do* or what the
board *says is true* — so it cannot lose progress or take an illegal action, and a
crash at any step resumes on the next wake.
