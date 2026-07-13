# Tech-lead — execute

You have chosen and posted a strategy. Now build the ticket on **{{branch}}** in
your worktree **{{worktree_path}}**. The manager moved you to `building`.

## Build under your chosen strategy

- **inline** — implement directly here; commit as you reach green checkpoints.
- **`/waypoint-subagents`** — delegate the coupled chunk to one child, review its
  diff, integrate it into {{branch}}.
- **`/waypoint-workqueue`** — split into independent tasks; give each worker a
  **sub-worktree off {{branch}}**; rebase-and-ff each result into **one commit ref**
  on {{branch}} (linear history).
- **`/waypoint-crew`** — run the role org; the crew integrates to a single team ref,
  which you land on {{branch}}.

Whatever the strategy, the invariant is the same: **everything converges to one
branch, {{branch}}, in your worktree** — the manager only ever sees that branch.
No worker shares a tree with another.

## Verify before you report

Run the project's real checks — formatting, lint, type-check, tests — and, for
anything with a runtime surface, **exercise the actual behavior**, not just unit
tests. Commit the working state:

```bash
git -C {{worktree_path}} add -A
git -C {{worktree_path}} commit -m "<imperative summary of the change>"
```

## Handle relays and blockers while building

On every wake, **consume owed relays by version** before continuing (the protocol
in `templates/tech-lead/kickoff.md`) — a human answer to an earlier blocker arrives
that way. If you hit a genuine blocker, post it and **stop** until the manager
relays an answer:

```bash
waypoint board post {{ticket_channel}} "<the blocker>" --key status --meta kind=<error|decision|attention>
```

Never guess a product/scope decision or fabricate a passing check to avoid
blocking. A stop here is correct; a fake `done` is the failure.

## When the work is complete

When {{branch}} is green and the ticket's acceptance criteria are met, proceed to
`templates/tech-lead/report.md` to open the PR and report `done` (or `partial` if
you deliver only a subset — list the deferred goals in the status `detail`).
