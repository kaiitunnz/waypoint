# Manager — integrate

A ticket is in `review_requested` with a PR at {{pr_url}}, or already in `merging`.
You are the **sole integrator** of {{trunk}} and the human is the **sole merge
authority**. Run the review-until-merge loop, then land the PR behind the
integration lease. Never merge on your own authority.

## Review-until-merge loop (human gated)

1. Post the PR to the human as an **approval** inbox item (link, summary, CI
   state from `gh pr view {{pr_url}} --json state,statusCheckRollup`). You are
   `--wake-on-inbox`-subscribed, so the answer wakes you.
2. On the answer:
   - **request-changes** → transition `review_requested → revising`, then relay the
     feedback to the lead **durably** (versioned `{{ticket_channel}}` post + a
     content-free nudge — see `templates/manager/monitor.md`). The lead addresses it
     (`templates/tech-lead/address-review.md`), re-pushes, and re-posts `done`; you
     move `revising → review_requested` and re-post the gate on the new head.
   - **merge** → go to "Land the PR" below.
   - **abort / latency-timeout** → `review_requested → abandoned`, note it on the
     ticket, reap the subtree.
3. Loop until the human merges or aborts.

## Land the PR (only on a human merge decision)

Acquire the lease first — you are the only session that advances {{trunk}}:

```bash
waypoint manager lock acquire --owner {{manager_session_id}}      # --ttl-seconds defaults to the manifest
waypoint manager ticket transition {{ticket_id}} --to merging --pr-url {{pr_url}}
```

Reconcile against the PR before doing anything irreversible — a prior turn may have
already merged it:

```bash
gh pr view {{pr_url}} --json state,mergeStateStatus,statusCheckRollup
# state == "MERGED" → the merge already happened; skip to Finalize.
```

**Rebase onto the advanced trunk, then merge.** Rebase in the ticket's worktree,
where {{branch}} is checked out, so it never contends with your main checkout:

```bash
git -C {{worktree_path}} fetch origin {{trunk}}
git -C {{worktree_path}} rebase origin/{{trunk}}
# Trivial conflicts only (lockfiles, generated files): resolve, `git add`, `git rebase --continue`.
# A SEMANTIC conflict → `git rebase --abort` and release the lease (`manager lock
#   release`) so the single merging lane frees at once. If a build slot is free,
#   transition `merging → revising` and relay the conflict to the lead; if the slot
#   cap is full, transition `merging → blocked` instead and re-delegate the
#   rebase-and-resolve when a slot frees (never leave the ticket stuck in `merging`
#   holding the lane). Do NOT hand-resolve logic yourself.
git -C {{worktree_path}} push --force-with-lease
```

Confirm CI is green if `require_ci_green`, then merge (only if not already
`MERGED`):

```bash
gh pr merge {{pr_url}} --squash --delete-branch   # or --auto so CI-gating never blocks a turn
```

## Release the lease on EVERY exit

Release whether you merged, deferred, bounced to `revising`, or blocked — the lease
must never leak:

```bash
waypoint manager lock release --owner {{manager_session_id}}
```

A dead owner's lease is recoverable only by `waypoint manager lock steal --owner
… ` **after the TTL expires**.

## Finalize

- **Full completion** → `merging → merged`. Terminal.
- **Partial completion** (`is_partial` true) → `merging → deferred`. Terminal.
  Spawn follow-up tickets for the unmet goals **only here**, once the subset has
  merged, with a deterministic id/dedup key so a re-run does not double-create:
  ```bash
  waypoint manager ticket add "follow-up: <goal>" --id ticket-{{ticket_id}}-f1 \
    --priority {{priority}} --dep {{ticket_id}}
  ```
- **CI red / needs human** → release lease, `merging → blocked`, escalate.
- **Semantic conflict** → released lease, `merging → revising` (above).

On a terminal merged/deferred, reap the ticket's whole subtree **after**
integration (`terminate` preserves worktrees for resume; only `delete`/`reap`
removes them). Scope to this ticket by its
recorded lead sid — reap the lead's descendants, then delete the lead itself
(`--spawned-by <lead>` reaps what the lead spawned, not the lead):

```bash
lead=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.lead_session_id')
waypoint sessions reap --spawned-by "$lead" --recursive --prune-branches   # the lead's workers; branch landed on {{trunk}}
waypoint sessions delete "$lead" --force                                    # the lead itself (removes its worktree)
```

Post a one-line outcome to your `{{org_channel}}` channel and return to
`templates/manager/loop-cycle.md`.
