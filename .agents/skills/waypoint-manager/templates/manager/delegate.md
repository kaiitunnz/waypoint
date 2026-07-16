# Manager — delegate

A ticket is `ready` and `manager next` recommended `delegate`. The tech-lead runs
**in your own working tree {{repo_dir}}** on the ticket branch — there is no
per-ticket worktree. **Record intent before spawning** so a crash resumes rather
than double-spawns.

The ticket already carries the `spec_ref` triage set — a produced-and-approved
PRD/RFC, a pass-through input PRD/RFC, or none (trivial direct-instruction). The
spawn is identical for all three; the brief carries whatever `spec_ref` the ticket
holds.

## 1. Reconcile first

Before anything, confirm no live lead already owns this ticket:

```bash
waypoint sessions list --spawned-by {{manager_session_id}} --recursive \
  | jq -r '.sessions[] | select(.title=="subagent:ticket-{{ticket_id}}:tech-lead") | "\(.id) \(.status)"'
```

- A **live** match → adopt it (record `--lead-session-id`); do **not** spawn.
- A **dead** match with work on `{{branch}}` → this is a resume, not an initial
  delegate. Check the branch out in your tree, then re-spawn onto it:
  ```bash
  waypoint sessions terminate <dead-sid>                       # keep the branch + its commits
  waypoint manager ticket transition {{ticket_id}} --to <same-state> --reason lead-died   # self-loop, spends lead_restarts
  git -C {{repo_dir}} checkout {{branch}}                       # the branch holds the committed work
  new=$(waypoint sessions start {{tech_lead_launch}} \
    --cwd {{repo_dir}} \
    --title "subagent:ticket-{{ticket_id}}:tech-lead" \
    --spawner-session-id {{manager_session_id}} | jq -r .session.id)   # never --worktree: the lead shares your tree
  waypoint manager ticket update {{ticket_id}} --lead-session-id "$new"
  waypoint sessions wake-on-board "$new" --channels {{ticket_channel}}   # relays wake it; the manager owns the inbox
  waypoint sessions send "$new" "$(waypoint manager render --role tech_lead --step brief --ticket {{ticket_id}})"
  ```
  Past `max_lead_restarts` the self-loop is rejected (`409`) — escalate `--to blocked`.
- A stale `{{branch}}` from an incomplete reap with **no** live session → delete it
  before spawning. It cannot be checked out, so return the tree to `{{trunk}}` first:
  `git -C {{repo_dir}} checkout {{trunk}} && git -C {{repo_dir}} branch -D {{branch}}`.

An already-`delegated` ticket with no live lead is an interrupted delegate: the state
and dedup key are recorded, so skip step 2 and resume at step 3, where the idempotent
cut checks out a branch a prior pass left behind.

## 2. Record intent (the dedup key) — transition first

`{{branch}}` is this ticket's branch, `ticket/{{ticket_id}}` by convention.

```bash
waypoint manager ticket transition {{ticket_id}} --to delegated \
  --intended-lead-title "subagent:ticket-{{ticket_id}}:tech-lead" \
  --branch {{branch}}
```

Bumps `attempts` and reserves the unique title. If the turn dies here, the next
drain sees a `delegated` ticket with no live lead and re-spawns into the reserved
title exactly once.

## 3. Cut the branch in your tree, then spawn the lead

Cut the ticket branch, then spawn the lead into your tree with **no** `--worktree`.
The cut is idempotent: a prior delegate pass may have already cut `{{branch}}`, so check
it out when it exists and create it from `{{trunk}}` otherwise.

```bash
git -C {{repo_dir}} checkout {{trunk}}
if git -C {{repo_dir}} rev-parse --verify --quiet {{branch}} >/dev/null; then
  git -C {{repo_dir}} checkout {{branch}}                 # resume the branch a prior pass cut
else
  git -C {{repo_dir}} checkout -b {{branch}} {{trunk}}
fi
# {{tech_lead_launch}} is the tech-lead's launch args, baked from roles.tech_lead.
sid=$(waypoint sessions start {{tech_lead_launch}} \
  --cwd {{repo_dir}} \
  --title "subagent:ticket-{{ticket_id}}:tech-lead" \
  --spawner-session-id {{manager_session_id}} | jq -r .session.id)
waypoint manager ticket update {{ticket_id}} --lead-session-id "$sid"
waypoint sessions wake-on-board "$sid" --channels {{ticket_channel}}   # relays wake it; the manager owns the inbox
```

If the model/permission were wrong the lead dies on turn 1; confirm it actually
started (`waypoint sessions show "$sid"` → `starting`/`idle`/`running`, not
`exited`/`error`). A turn-1 death is a spawn failure with no committed work — reap the
failed session so its reserved title frees for the retry, clear the dead lead ref off
the ticket, and return the tree to `{{trunk}}`, dropping the empty branch:

```bash
waypoint sessions delete "$sid" --force                       # free the reserved title
waypoint manager ticket update {{ticket_id}} --lead-session-id ""   # drop the dead lead ref
git -C {{repo_dir}} checkout {{trunk}} && git -C {{repo_dir}} branch -D {{branch}}
```

Then retry the delegate while budget remains: move `delegated → ready` and add the
ticket to this drain's `tried` set (a later drain re-delegates). When the delegate
budget is exhausted that move is rejected (`409`) — escalate to the human instead. Post
the failure as a keyless `kind=decision` log entry (the durable source the gate is built
from), clear the stale `branch` field (the git branch is already dropped), then move
`delegated → blocked`:

```bash
waypoint manager ticket transition {{ticket_id}} --to ready --reason "retry after spawn failure" \
  || {
    waypoint board post {{ticket_channel}} \
      "delegation failed (a misconfigured launch?); fix the launch config then retry, or abandon. Options: retry; abandon." \
      --meta kind=decision
    waypoint manager ticket update {{ticket_id}} --branch ""
    waypoint manager ticket transition {{ticket_id}} --to blocked --reason "delegate budget exhausted"
  }
```

On the escalation, the next drain sees a branch-less `blocked` ticket with no gate item
and re-opens the decision gate from that entry
(`{{templates_dir}}/manager/monitor.md`); on the human's **retry** the gate resets the
delegate budget and revives the ticket, on **abandon** it ends.

## 4. Send the brief

Render the tech-lead brief with this ticket's values and send it:

```bash
waypoint sessions send "$sid" "$(waypoint manager render --role tech_lead --step brief --ticket {{ticket_id}})"
```

The brief tells the lead to post `accepted`, run the **strategy gate** — an explicit,
justified choice among `/waypoint-subagents`, `/waypoint-workqueue`, and
`/waypoint-crew` — post its chosen strategy to `{{ticket_channel}}`, and build.

Then return to `{{templates_dir}}/manager/loop-cycle.md` and continue the drain — do not
block waiting for the lead; its progress wakes you via `{{ticket_channel}}`. A later wake
observes the strategy post and moves `delegated → building` (see
`{{templates_dir}}/manager/monitor.md`).
