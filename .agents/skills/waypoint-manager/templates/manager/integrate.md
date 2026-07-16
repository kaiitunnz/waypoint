# Manager — integrate

{{#if integration_mode == pr}}
A ticket is in `review_requested` with a PR at {{pr_url}}. The human is the **sole
merge authority**: you run the review-until-merge loop and **observe** the human's
merge — you never merge on your own authority.
{{/if}}
{{#if integration_mode == local}}
A ticket is in `review_requested` with its work committed on {{branch}}. The human is
the **sole merge authority**: you run the review-until-merge loop, and on the human's
approval fast-forward {{trunk}} onto {{branch}} — you never merge without that approval.
{{/if}}
`{{branch}}` is checked out in {{repo_dir}}.

## Review-until-merge loop (human gated)

1. Post the work to the human as an **approval** inbox item. You are `--wake-on-inbox`-
   subscribed, so the answer wakes you.
{{#if integration_mode == pr}}
   ```bash
   item=$(waypoint inbox list --status open --q "{{ticket_channel}}: " \
     | jq -r '[.items[] | select((.subject|startswith("{{ticket_channel}}: ")) and (.subject|endswith("— PR review"))) | .id] | first // empty')   # adopt an open gate a crash left behind
   [ -n "$item" ] || item=$(waypoint inbox post --json - <<'JSON' | jq -r '.item.id'
   { "subject": "{{ticket_channel}}: {{ticket_title}} — PR review",
     "blocks": [
       { "type": "markdown", "text": "<PR link {{pr_url}}, summary, CI state from gh pr view>" },
       { "type": "approval", "prompt": "Merge this PR?",
         "options": ["merge", "request-changes", "abort"], "required": true } ] }
   JSON
   )
   waypoint manager ticket update {{ticket_id}} --inbox-item "$item"
   ```
{{/if}}
{{#if integration_mode == local}}
   ```bash
   item=$(waypoint inbox list --status open --q "{{ticket_channel}}: " \
     | jq -r '[.items[] | select((.subject|startswith("{{ticket_channel}}: ")) and (.subject|endswith("— merge review"))) | .id] | first // empty')   # adopt an open gate a crash left behind
   [ -n "$item" ] || item=$(waypoint inbox post --json - <<'JSON' | jq -r '.item.id'
   { "subject": "{{ticket_channel}}: {{ticket_title}} — merge review",
     "blocks": [
       { "type": "markdown", "text": "<branch {{branch}}: git log --oneline and git diff --stat vs {{trunk}}>" },
       { "type": "approval", "prompt": "Fast-forward {{trunk}} onto {{branch}}?",
         "options": ["approve", "request-changes", "abort"], "required": true } ] }
   JSON
   )
   waypoint manager ticket update {{ticket_id}} --inbox-item "$item"
   ```
{{/if}}
2. On a later wake, read the gate answer from the ticket's recorded `inbox_item_id`.
   Act only once the item has resolved, then branch on the approval decision:
   ```bash
   item=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.inbox_item_id // empty')
   [ -n "$item" ] && answer=$(waypoint inbox get "$item")
   if [ "$(echo "$answer" | jq -r '.item.status')" = resolved ]; then
     decision=$(echo "$answer" | jq -r '.item.blocks[] | select(.type=="approval") | .answer.decision // empty')
   fi
   ```
   Branch on `$decision`:
   - **request-changes** → relay the round to the lead, overwrite the consumed `done`
     cell, and move to `revising`: post the human's requested changes (their
     `reply.notes`) to `{{ticket_channel}}` as the durable payload, overwrite the
     `status` cell to `kind=progress`, transition `review_requested → revising`, then
     send the rendered address-review instructions:
     ```bash
     notes=$(echo "$answer" | jq -r '[.item.blocks[].reply.notes // empty] | map(select(. != "")) | join("\n")')
     waypoint board post {{ticket_channel}} "${notes:-address the review}" --meta kind=relay
     waypoint board post {{ticket_channel}} "revising: addressing the review round" --key status --meta kind=progress
     waypoint manager ticket transition {{ticket_id}} --to revising --reason request-changes
     lead=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.lead_session_id')
     waypoint sessions send "$lead" \
       "$(waypoint manager render --role tech_lead --step address-review --ticket {{ticket_id}})"
     ```
     The lead addresses the feedback, re-pushes, and re-posts `done`; you move
     `revising → review_requested` and re-post the gate on the new head.
{{#if integration_mode == pr}}
   - **merge** → the human merges on GitHub; record it (below).
{{/if}}
{{#if integration_mode == local}}
   - **approve** → fast-forward {{trunk}} onto {{branch}} in the tree; record it (below).
{{/if}}
   - **abort** → `review_requested → abandoned`, note it on the ticket, then reap the
     subtree and free the tree (Finalize).
3. Loop until the ticket is merged or aborted. A silent latency-timeout is abandoned by
   the `latency_timeouts` reconcile path in `{{templates_dir}}/manager/loop-cycle.md`.

## Record the merge

{{#if integration_mode == pr}}
Each drain, reconcile the PR against GitHub — the human may have merged it since your
last turn:

```bash
gh pr view {{pr_url}} --json state,mergeStateStatus,statusCheckRollup
```

- `state == "MERGED"` → record the terminal: full completion → `review_requested →
  merged`; partial completion (`is_partial`) → `review_requested → deferred`.

The single shared tree serializes builds, and trunk advances only on a merge the human
performs (or, opt-in, one they ask you to perform).

### Merge on the human's behalf (opt-in only)

Only if the human explicitly asks you to merge for them: reconcile first (skip if
already `MERGED`); if the branch needs it, rebase onto the advanced trunk in your tree
— trivial lockfile/generated conflicts only (`git add`, `git rebase --continue`). A
**semantic** conflict is the lead's to resolve: abort the rebase, relay it, overwrite
the consumed `done` cell, and move to `revising` (never hand-resolve logic yourself):

```bash
git -C {{repo_dir}} rebase --abort
waypoint board post {{ticket_channel}} "rebase hit a semantic conflict on {{trunk}}; resolve it and re-report done" --meta kind=relay
waypoint board post {{ticket_channel}} "revising: resolving a rebase conflict" --key status --meta kind=progress
waypoint manager ticket transition {{ticket_id}} --to revising --reason "semantic rebase conflict"
lead=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.lead_session_id')
waypoint sessions send "$lead" "$(waypoint manager render --role tech_lead --step address-review --ticket {{ticket_id}})"
```

On a clean rebase the ticket stays `review_requested`. Merge it and record the terminal
in the same step; when `require_ci_green` is `true` (here `{{require_ci_green}}`), wait
for green CI first. The semantic-conflict path leaves the ticket in `revising`; the
guard below then skips both the merge and its record, and the `state == MERGED`
reconcile above records the terminal on a later drain once the re-reviewed work merges:

```bash
if [ "$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.state')" = review_requested ]; then
  git -C {{repo_dir}} push --force-with-lease       # only if you rebased
  gh pr merge {{pr_url}} --squash --delete-branch    # or --auto so CI-gating never blocks a turn
  waypoint manager ticket transition {{ticket_id}} --to merged    # or --to deferred for a partial delivery
fi
```
{{/if}}
{{#if integration_mode == local}}
On the human's **approve**, fast-forward {{trunk}} onto {{branch}} in the shared tree,
then record the terminal. Two guards precede the fast-forward: the branch's ancestry
(the branch is your durable witness, so a resumed turn re-derives the merge and never
repeats it), and — when `require_ci_green` is `true` (here `{{require_ci_green}}`) — the
lead's reported `checks` on the status cell. Fast-forward only when `checks` reads
`green`; a missing or non-`green` value sends the ticket back to the lead to fix, keeping
the tree on `{{branch}}` for the lead to build on (only the fast-forward path checks out
`{{trunk}}`).

```bash
checks=$(waypoint board read {{ticket_channel}} --key status --json | jq -r '.cells[0].metadata.checks // "missing"')
if [ "{{require_ci_green}}" = true ] && [ "$checks" != green ]; then
  waypoint board post {{ticket_channel}} "local checks are not green (checks=$checks); fix and re-report done" --meta kind=relay
  waypoint board post {{ticket_channel}} "revising: fixing checks" --key status --meta kind=progress
  waypoint manager ticket transition {{ticket_id}} --to revising --reason "checks not green: $checks"
  lead=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.lead_session_id')
  waypoint sessions send "$lead" "$(waypoint manager render --role tech_lead --step address-review --ticket {{ticket_id}})"
else
  git -C {{repo_dir}} checkout {{trunk}}
  git -C {{repo_dir}} merge-base --is-ancestor {{branch}} {{trunk}} \
    || git -C {{repo_dir}} merge --ff-only {{branch}}   # ff only when the branch is not already merged
fi
```

On the send-back, the lead fixes and re-reports `done`; you move `revising →
review_requested` and re-open the merge gate on the new head. On the fast-forward, record
the terminal (below).

Record `review_requested → merged` (full) or `→ deferred` (partial) **after** the
fast-forward lands, so a crash between the merge and the record re-derives the merge
from the branch's ancestry on the next drain.
{{/if}}

## Finalize

On any terminal for a ticket that reached the tree (`merged`/`deferred`/`abandoned`),
reap the ticket's whole subtree **after** the merge and free the tree for the next
ticket. Scope to this ticket by its recorded lead sid — reap the lead's descendants
(their worker sub-worktrees prune with them), then delete the lead itself, then return
your tree to `{{trunk}}` and drop the branch. Each step is guarded (no-op when the
ticket never got a branch or lead):

```bash
lead=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.lead_session_id // empty')
if [ -n "$lead" ]; then
  for s in $(waypoint sessions list --spawned-by "$lead" --recursive | jq -r '.sessions[].id'); do
    waypoint sessions delete "$s" --force --prune-branches    # workers had sub-worktrees; prune them
  done
  waypoint sessions delete "$lead" --force      # the lead had no worktree (it shared your tree)
fi
git -C {{repo_dir}} checkout {{trunk}}
{{#if integration_mode == pr}}
git -C {{repo_dir}} pull --ff-only origin {{trunk}}           # sync trunk (the just-merged commit, if any)
{{/if}}
git -C {{repo_dir}} rev-parse --verify --quiet {{branch}} \
  && git -C {{repo_dir}} branch -D {{branch}} || true         # no-op if the branch was never cut / already dropped
```

**Follow-ups on `deferred`** — a partial completion does **not** auto-create follow-up
tickets. Post an inbox confirmation listing the proposed follow-ups and create them
only on the human's approval, with a deterministic id/dedup key so a re-run does not
double-create:

```bash
waypoint board post {{tickets_channel}} "<goal>" --key ticket:{{ticket_id}}-f1   # registry cell, like intake
waypoint manager ticket add "follow-up: <goal>" --id {{ticket_id}}-f1 \
  --priority {{priority}} --dep {{ticket_id}}
```

Post a one-line outcome to your `{{org_channel}}` channel and return to
`{{templates_dir}}/manager/loop-cycle.md`; redeploy the stack here if the project needs it
(the tree is back on `{{trunk}}`).
