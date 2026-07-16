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
   item=$(waypoint inbox post --json - <<'JSON' | jq -r '.item.id'
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
   item=$(waypoint inbox post --json - <<'JSON' | jq -r '.item.id'
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
   - **request-changes** → transition `review_requested → revising`, then relay the
     round to the lead: post the human's requested changes (their `reply.notes`) to
     `{{ticket_channel}}` as the durable payload, and send the rendered address-review
     instructions:
     ```bash
     notes=$(echo "$answer" | jq -r '[.item.blocks[].reply.notes // empty] | map(select(. != "")) | join("\n")')
     waypoint board post {{ticket_channel}} "${notes:-address the review}" --meta kind=relay
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
— trivial lockfile/generated conflicts only (`git add`, `git rebase --continue`); a
**semantic** conflict → `git rebase --abort`, transition `review_requested → revising`,
and relay it to the lead (never hand-resolve logic yourself). Then merge; when
`require_ci_green` is `true` (here `{{require_ci_green}}`), wait for green CI first:

```bash
git -C {{repo_dir}} push --force-with-lease       # only if you rebased
gh pr merge {{pr_url}} --squash --delete-branch    # or --auto so CI-gating never blocks a turn
```

Record `review_requested → merged` (or `→ deferred`) in the same step.
{{/if}}
{{#if integration_mode == local}}
On the human's **approve**, fast-forward {{trunk}} onto {{branch}} in the shared tree,
then record the terminal. The branch is your durable witness: check its ancestry first,
so a resumed turn re-derives the merge and never repeats it.

```bash
git -C {{repo_dir}} checkout {{trunk}}
git -C {{repo_dir}} merge-base --is-ancestor {{branch}} {{trunk}} \
  || git -C {{repo_dir}} merge --ff-only {{branch}}   # ff only when the branch is not already merged
```

When `require_ci_green` is `true` (here `{{require_ci_green}}`), gate the fast-forward on
the lead's reported checks: fast-forward only when the status cell's `checks` reads
`green`; a missing or non-`green` value blocks the merge — escalate to the inbox rather
than fast-forward.

```bash
waypoint board read {{ticket_channel}} --key status --json | jq -r '.cells[0].metadata.checks'   # must be "green"
```

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
