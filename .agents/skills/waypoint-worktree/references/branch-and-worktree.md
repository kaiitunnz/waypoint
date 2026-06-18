# Branch And Worktree State

The branch you are on, and whether the branch you want is free, are both shared
state on a multi-session host. Confirm them rather than assuming.

## Is the checkout current?

`main` can advance between your turns as other sessions land work. A feature
branch that is behind `main` is missing those fixes — and because `waypointctl
restart` deploys the **currently checked-out tree** (not `main`), running the
stack on a stale branch silently serves old code (see the `waypointctl` skill).

Check how far the branch diverges before committing or restarting:

```bash
git fetch origin
git rev-list --left-right --count origin/main...HEAD
#            ^ commits on main not in HEAD   ^ commits on HEAD not on main
```

A nonzero left count means the branch is behind `main`. If the branch needs the
running stack, or its work overlaps recently-merged fixes, fast-forward or rebase
it onto `main` first — feature-branch files rarely overlap merged fixes, so this
is usually clean.

## Branch locked by a sibling worktree

A branch can be checked out in only one worktree at a time. On a shared host the
branch you want — often a PR branch — may already be checked out by another
session, and `git switch` / `gh pr checkout` fails:

```
fatal: '<branch>' is already used by worktree at '<path>'
```

Do not fight it (do not force, detach, or delete the other worktree's checkout —
that is another session's workspace). Instead **work from where the branch already
lives**:

```bash
git worktree list                 # find the path holding the branch
git -C <that-path> status         # operate there, or read the diff from it
```

For a read-only need (reviewing a PR), inspect the diff without checking the
branch out at all — e.g. `gh pr diff <n>`, or `git -C <that-path> diff`.

## Prefer moving an existing branch forward

Each new branch is another name that can collide with another session's work and
another thing to reap later. When you can extend an existing branch instead of
cutting a new one, do — it keeps the shared namespace small and the history
legible.
