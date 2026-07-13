# Manager — integrate

A ticket is in `review_requested` with a PR at {{pr_url}}, or already in `merging`.
You are the **sole integrator** of {{trunk}} and the human is the **sole merge
authority**. Run the review-until-merge loop, then land the PR behind the
integration lease. Never merge on your own authority. `{{branch}}` is checked out in
{{repo_dir}}.

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
     ticket, then reap the subtree and release the tree (Finalize steps below).
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

**Rebase onto the advanced trunk, then merge**, in your tree where {{branch}} is
checked out:

```bash
git -C {{repo_dir}} checkout {{branch}}
git -C {{repo_dir}} fetch origin {{trunk}}
git -C {{repo_dir}} rebase origin/{{trunk}}
# Trivial conflicts only (lockfiles, generated files): resolve, `git add`, `git rebase --continue`.
# A SEMANTIC conflict → `git rebase --abort` and release the lease (`manager lock
#   release`), transition `merging → revising`, and relay the conflict to the lead.
#   Do NOT hand-resolve logic yourself.
git -C {{repo_dir}} push --force-with-lease
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
  waypoint board post {{tickets_channel}} "<goal>" --key ticket:{{ticket_id}}-f1   # registry cell, like intake
  waypoint manager ticket add "follow-up: <goal>" --id {{ticket_id}}-f1 \
    --priority {{priority}} --dep {{ticket_id}}
  ```
- **CI red / needs human** → release lease, `merging → blocked`, escalate.
- **Semantic conflict** → released lease, `merging → revising` (above).

On any terminal for a ticket that reached the tree (`merged`/`deferred` here, or
`abandoned` from the abort path above), reap the ticket's whole subtree **after**
integration and free the tree for the next ticket. Scope to this ticket by its
recorded lead sid — reap the lead's descendants (their worker sub-worktrees prune
with them), then delete the lead itself, then return your tree to `{{trunk}}` and
drop the branch. Each step is guarded (no-op when the ticket never got a branch or
lead):

```bash
lead=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.lead_session_id // empty')
if [ -n "$lead" ]; then
  for s in $(waypoint sessions list --spawned-by "$lead" --recursive | jq -r '.sessions[].id'); do
    waypoint sessions delete "$s" --force --prune-branches    # workers had sub-worktrees; prune them
  done
  waypoint sessions delete "$lead" --force      # the lead had no worktree (it shared your tree)
fi
git -C {{repo_dir}} checkout {{trunk}}
git -C {{repo_dir}} pull --ff-only origin {{trunk}}           # sync trunk (the just-merged commit, if any)
git -C {{repo_dir}} rev-parse --verify --quiet {{branch}} \
  && git -C {{repo_dir}} branch -D {{branch}} || true         # no-op if the branch was never cut / already dropped
```

Post a one-line outcome to your `{{org_channel}}` channel and return to
`templates/manager/loop-cycle.md`; redeploy the stack here if the project needs it
(the tree is back on `{{trunk}}`).
