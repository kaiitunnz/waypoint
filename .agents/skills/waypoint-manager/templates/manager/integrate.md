# Manager — integrate

A ticket is in `review_requested` with a PR at {{pr_url}}. The human is the **sole
merge authority**: you run the review-until-merge loop and **observe** the human's
merge — you never merge on your own authority. `{{branch}}` is checked out in
{{repo_dir}}.

## Review-until-merge loop (human gated)

1. Post the PR to the human as an **approval** inbox item, subject
   `{{ticket_channel}}: {{ticket_title}} — PR review`. You are `--wake-on-inbox`-
   subscribed, so the answer wakes you.
   ```bash
   waypoint inbox post --json - <<'JSON'
   { "subject": "{{ticket_channel}}: {{ticket_title}} — PR review",
     "blocks": [
       { "type": "markdown", "text": "<PR link {{pr_url}}, summary, CI state from gh pr view>" },
       { "type": "approval", "prompt": "Merge this PR?", "required": true } ] }
   JSON
   ```
2. On a later wake, read the answer from the inbox by its `{{ticket_channel}}:` subject
   (`item=$(waypoint inbox list --status resolved --q "{{ticket_channel}}:" | jq -r
   --arg p "{{ticket_channel}}:" '[.items[] | select(.subject | startswith($p))][0].id')`;
   when `item` is non-empty, `waypoint inbox get "$item"`), then branch on the decision:
   - **request-changes** → transition `review_requested → revising`, then relay the
     round to the lead: post the human's requested changes to `{{ticket_channel}}`
     as the durable payload, and send the rendered address-review instructions:
     ```bash
     waypoint board post {{ticket_channel}} "<the human's requested changes>" --meta kind=relay
     lead=$(waypoint manager ticket show {{ticket_id}} | jq -r '.ticket.lead_session_id')
     waypoint sessions send "$lead" \
       "$(waypoint manager render --role tech_lead --step address-review --ticket {{ticket_id}})"
     ```
     The lead addresses the feedback, re-pushes, and re-posts `done`; you move
     `revising → review_requested` and re-post the gate on the new head.
   - **merge** → the human merges on GitHub; record it (below).
   - **abort** → `review_requested → abandoned`, note it on the ticket, then reap the
     subtree and free the tree (Finalize).
3. Loop until the PR is merged or the ticket is aborted. A silent latency-timeout is
   abandoned by the `latency_timeouts` reconcile path in
   `{{templates_dir}}/manager/loop-cycle.md`.

## Record the merge

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
git -C {{repo_dir}} pull --ff-only origin {{trunk}}           # sync trunk (the just-merged commit, if any)
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
