# Manager — delegate

A ticket is `ready` and `manager next` recommended `delegate`. The tech-lead runs
**in your own working tree {{repo_dir}}** on the ticket branch — there is no
per-ticket worktree. **Record intent before spawning** so a crash resumes rather
than double-spawns.

The ticket already carries the `spec_ref` triage set — a produced-and-approved
PRD/RFC, a pass-through input PRD/RFC, or none (trivial direct-instruction). The
spawn is identical for all three; the kickoff renders whatever `spec_ref` the ticket
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
  waypoint sessions send "$new" "$(render templates/tech-lead/kickoff.md)"   # re-reads the log + owed relays
  ```
  Past `max_lead_restarts` the self-loop is rejected (`409`) — escalate `--to blocked`.
- A stale `{{branch}}` from an incomplete reap with **no** live session → delete it
  before spawning. It cannot be checked out, so return the tree to `{{trunk}}` first:
  `git -C {{repo_dir}} checkout {{trunk}} && git -C {{repo_dir}} branch -D {{branch}}`.

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

The tree is on `{{trunk}}`. Cut the ticket branch, then spawn the lead into your
tree with **no** `--worktree`:

```bash
git -C {{repo_dir}} checkout {{trunk}}
git -C {{repo_dir}} checkout -b {{branch}} {{trunk}}
# {{tech_lead_launch}} expands from roles.tech_lead in the manifest — a preset: role
# becomes `--preset <name>`, an inline launch: becomes `--backend <b> --model <m>
# --permission-mode <p>`. Never hardcode a preset/model here.
sid=$(waypoint sessions start {{tech_lead_launch}} \
  --cwd {{repo_dir}} \
  --title "subagent:ticket-{{ticket_id}}:tech-lead" \
  --spawner-session-id {{manager_session_id}} | jq -r .session.id)
waypoint manager ticket update {{ticket_id}} --lead-session-id "$sid"
waypoint sessions wake-on-board "$sid" --channels {{ticket_channel}}   # relays wake it; the manager owns the inbox
```

If the model/permission were wrong the lead dies on turn 1; confirm it actually
started (`waypoint sessions show "$sid"` → `starting`/`idle`/`running`, not
`exited`/`error`). A turn-1 death is a spawn failure with no committed work — return
the tree to `{{trunk}}`, drop the empty branch, then move `delegated → ready` (still
under budget) or `→ blocked` (budget exhausted) and add the ticket to this drain's
`tried` set:

```bash
git -C {{repo_dir}} checkout {{trunk}} && git -C {{repo_dir}} branch -D {{branch}}
```

## 4. Send the kickoff

Render the tech-lead kickoff with this ticket's values and send it:

```bash
waypoint sessions send "$sid" "$(render templates/tech-lead/kickoff.md)"
```

The kickoff (`templates/tech-lead/kickoff.md`) tells the lead to investigate, then
run the **strategy gate** (`templates/tech-lead/strategy-gate.md`) — an explicit,
justified choice among `/waypoint-subagents`, `/waypoint-workqueue`, and
`/waypoint-crew` — post its `accepted` + chosen strategy to `{{ticket_channel}}`,
and begin. When you observe that post, move `delegated → building`.

Then return to `templates/manager/loop-cycle.md` and continue the drain — do not
block waiting for the lead; its progress wakes you via `{{ticket_channel}}`.
