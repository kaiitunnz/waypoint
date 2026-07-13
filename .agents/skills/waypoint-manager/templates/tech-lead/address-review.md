# Tech-lead — address review

A review round arrived. The manager relayed the human's requested changes to
{{ticket_channel}} and moved the ticket to `revising`, and you were woken. Address
the feedback, re-push, and re-report — then park again for the next round. Repeat
until the human merges or aborts.

## 1. Consume the relayed feedback (by version)

Read the owed relay from the durable log — do not act on the nudge alone:

```bash
waypoint board log {{ticket_channel}} --grep relay --since <highest relay_version you've acted on>
```

Take the post whose `relay_version` exceeds the highest you have already handled,
act on it **once**, and record that version. If several rounds queued while you
were away, apply them in version order.

## 2. Address each finding (inline — do not call a personal skill)

Do not assume any `/address-review` skill is installed. Work the feedback yourself,
finding by finding, in your worktree **{{worktree_path}}** on **{{branch}}**:

- For each requested change, decide **accept** or **push back with reasoning** —
  do not silently ignore one. Implement the accepted ones.
- Where you disagree, note the rationale in your re-report rather than making the
  change; the human decides on the next round.
- Re-run the project's checks and re-exercise the affected behavior after the
  edits — a review fix that breaks a test is not done.

```bash
git -C {{worktree_path}} add -A
git -C {{worktree_path}} commit -m "<what the review round changed>"
```

## 3. Rebase onto trunk if it moved (inline — do not call a personal skill)

If {{trunk}} advanced under you, rebase before re-pushing so the PR stays
mergeable. Resolve only **trivial** conflicts (lockfiles, generated files); a
**semantic** conflict is a blocker — post `kind=decision` and stop rather than
guessing:

```bash
git -C {{worktree_path}} fetch origin {{trunk}}
git -C {{worktree_path}} rebase origin/{{trunk}}
# trivial conflict → resolve, `git add`, `git rebase --continue`
# semantic conflict → `git rebase --abort`, post kind=decision, stop
git -C {{worktree_path}} push --force-with-lease
```

## 4. Re-report done on the new head

Re-post `done` with the new PR head so the manager re-opens the review gate on the
updated PR (`done` is re-posted on each new head during `revising`):

```bash
head=$(git -C {{worktree_path}} rev-parse HEAD)
waypoint board post {{ticket_channel}} "revised: <what changed>; ready for re-review" \
  --key status --meta kind=done --meta pr={{pr_url}} --meta commit=$head
```

Add a short note of anything you pushed back on. Then park idle. The manager moves
you `revising → review_requested` and takes the new head back to the human; the
next round wakes you here again. Do not merge, and do not reap yourself.
