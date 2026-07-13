# Manager — delegate

A ticket is `ready` and `manager next` recommended `delegate` (so a slot is free
and the `attempts` budget is not exhausted). Delegate it to an ephemeral tech-lead
in its own worktree. **Record intent before spawning** so a crash resumes rather
than double-spawns.

A `ready` ticket arrives by one of three routes set in triage
(`templates/manager/triage.md`), which differ only in the `spec_ref` you carry
into the kickoff — the spawn below is identical for all three:

- **produced-and-approved** — a prd-writer/rfc-writer wrote the spec and it passed
  the `spec_review` gate; `spec_ref` = the produced PRD/RFC.
- **pass-through** — a large/well-defined or impl-open input PRD, or an input RFC,
  used as-is (no writer, no gate); `spec_ref` = the input doc.
- **trivial direct-instruction** — no spec; the lead works from the ticket body.

The writer spawn (prd-writer / rfc-writer) lives in triage, not here; by the time
a ticket is `ready`, any authoring is done. The ticket record already carries the
right `spec_ref`, so the kickoff renders it unchanged.

## 1. Reconcile first

Before anything, confirm no live lead already owns this ticket:

```bash
waypoint sessions list --spawned-by {{manager_session_id}} --recursive \
  | jq -r '.sessions[] | select(.title=="subagent:ticket-{{ticket_id}}:tech-lead") | "\(.id) \(.status)"'
```

- A **live** match → adopt it (record `--lead-session-id`); do **not** spawn.
- A **dead** match with work on `{{branch}}` → this is a resume, not an initial
  delegate:
  ```bash
  waypoint sessions terminate <dead-sid>                       # keep the branch + worktree
  waypoint manager ticket transition {{ticket_id}} --to <same-state> --reason lead-died   # self-loop, spends lead_restarts
  new=$(waypoint sessions start {{tech_lead_launch}} \
    --cwd {{worktree_path}} \
    --title "subagent:ticket-{{ticket_id}}:tech-lead" \
    --spawner-session-id {{manager_session_id}} | jq -r .session.id)   # no --worktree: reuse the preserved branch
  waypoint manager ticket update {{ticket_id}} --lead-session-id "$new"
  waypoint sessions wake-on-board "$new" --channels {{ticket_channel}} --wake-on-inbox
  waypoint sessions send "$new" "$(render templates/tech-lead/kickoff.md)"   # re-reads the log + owed relays
  ```
  Past `max_lead_restarts` the self-loop is rejected (`409`) — escalate `--to blocked`.
- A stale `{{branch}}` from an incomplete reap with **no** live session → delete
  it before spawning: `git -C <repo-root> branch -D {{branch}}`.

## 2. Record intent (the dedup key) — transition first

`{{branch}}` is this ticket's branch, `ticket/{{ticket_id}}` by convention; the
runtime derives its sibling `{{worktree_path}}` when the lead is spawned (step 3).

```bash
waypoint manager ticket transition {{ticket_id}} --to delegated \
  --intended-lead-title "subagent:ticket-{{ticket_id}}:tech-lead" \
  --branch {{branch}}
```

This bumps `attempts` and reserves the unique title (the invariant now rejects any
second ticket claiming it). If the turn dies here, the next drain sees a
`delegated` ticket with no live lead and re-spawns into the reserved title —
exactly once.

## 3. Spawn the lead in its worktree

```bash
# {{tech_lead_launch}} expands from roles.tech_lead in the manifest (its preset:
# name or inline launch: flags) — never hardcode a preset/model here.
sid=$(waypoint sessions start {{tech_lead_launch}} \
  --cwd <repo-root> \
  --worktree {{branch}} --worktree-base {{trunk}} \
  --title "subagent:ticket-{{ticket_id}}:tech-lead" \
  --spawner-session-id {{manager_session_id}} | jq -r .session.id)
wt=$(waypoint sessions show "$sid" | jq -r '.session.cwd')   # the derived sibling worktree path
waypoint manager ticket update {{ticket_id}} --lead-session-id "$sid" --worktree-path "$wt"
waypoint sessions wake-on-board "$sid" --channels {{ticket_channel}} --wake-on-inbox
```

If the model/permission were wrong the lead dies on turn 1; confirm it actually
started (`waypoint sessions show "$sid"` → `running`/`working`, not `exited`/
`error`). A turn-1 death is a spawn failure — move `delegated → ready` (still under
budget) or `→ blocked` (budget exhausted), and add the ticket to this drain's
`tried` set.

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
