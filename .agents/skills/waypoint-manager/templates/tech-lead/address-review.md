# Tech-lead — address review

A review round arrived. The manager relayed the human's requested changes to
{{ticket_channel}} and moved the ticket to `revising`, and you were woken. Address
the feedback and re-report — then park again for the next round. Repeat until the
human merges or aborts.

## 1. Consume the relayed feedback (by version)

Read the owed relay from the durable log — do not act on the nudge alone:

```bash
waypoint board log {{ticket_channel}} --json | jq -c '[.[] | select(.metadata.kind == "relay")] | sort_by(.id) | .[]'   # oldest-first
```

Take the relay posts whose board-entry `id` exceeds the highest relay `id` you have
already handled, act on each **once**, and record that id. If several rounds queued
while you were away, apply them in id order.

## 2. Address each finding

Work the feedback finding by finding, in the manager's tree **{{repo_dir}}** on
**{{branch}}** (the manager has this branch checked out for you again):

- For each requested change, decide **accept** or **push back with reasoning** —
  do not silently ignore one. Implement the accepted ones.
- Where you disagree, note the rationale in your re-report rather than making the
  change; the human decides on the next round.
- Re-run the project's checks and re-exercise the affected behavior after the
  edits — a review fix that breaks a test is not done.

```bash
git -C {{repo_dir}} add -A
git -C {{repo_dir}} commit -s -m "<what the review round changed>"
```

{{#if integration_mode == pr}}
## 3. Rebase onto trunk if it moved

If {{trunk}} advanced under you, rebase before re-pushing so the PR stays
mergeable. Resolve only **trivial** conflicts (lockfiles, generated files); a
**semantic** conflict is a blocker — post `kind=decision` and stop rather than
guessing:

```bash
git -C {{repo_dir}} fetch origin {{trunk}}
git -C {{repo_dir}} rebase origin/{{trunk}}
# trivial conflict → resolve, `git add`, `git rebase --continue`
# semantic conflict → `git rebase --abort`, post kind=decision, stop
git -C {{repo_dir}} push --force-with-lease
```
{{/if}}

{{#if integration_mode == pr}}
## 4. Re-report done on the new head
{{/if}}
{{#if integration_mode == local}}
## 3. Re-report done on the new head
{{/if}}

Re-post `done` on the new head so the manager re-opens the review gate on the updated
head (`done` is re-posted on each new head during `revising`):

```bash
head=$(git -C {{repo_dir}} rev-parse HEAD)
{{#if integration_mode == pr}}
waypoint board post {{ticket_channel}} "revised: <what changed>; ready for re-review" \
  --key status --meta kind=done --meta pr={{pr_url}} --meta commit=$head
{{/if}}
{{#if integration_mode == local}}
waypoint board post {{ticket_channel}} "revised: <what changed>; ready for re-review" \
  --key status --meta kind=done --meta commit=$head --meta checks=green
{{/if}}
```

Add a short note of anything you pushed back on. Then park idle. The manager moves
you `revising → review_requested` and takes the new head back to the human; the
next round wakes you here again. Do not merge, and do not reap yourself.
