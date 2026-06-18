---
name: waypoint-worktree
description: Use when committing, branching, or opening a PR from inside a Waypoint session, where the working tree is shared across live sessions and shifts between turns — `main` advances under you, edits get stranded in a stash, or a branch is already checked out in a sibling worktree. Verify the staged diff is complete and self-consistent and the checkout is current before committing.
---

# Waypoint Worktree Safety

A Waypoint host runs many sessions over the same repository at once. That makes
the working tree **shared, mutable, and not yours alone**: between two of your
turns, `main` can advance, another session can switch branches or stash edits,
and a branch you want may already be checked out in a sibling worktree. The
ordinary single-developer assumption — "the tree is exactly how I left it" — does
not hold here, and acting on it ships broken or stale commits.

This skill is the pre-flight for any write to git history in that environment. It
does not replace `/make-commits`, `/create-pr`, or the host's commit conventions
— run those as usual; this is the safety check you do **first**.

## The two failure modes to rule out

1. **The tree drifted under you.** Edits you made earlier in the session may no
   longer be in the working tree — reverted, or stranded in a `git stash` while
   the tree rolled back to a pre-edit state. Committing then yields an internally
   inconsistent change: code that references a symbol/class/file whose defining
   edit never made it in. See `references/before-committing.md`.
2. **The branch or checkout is not what you assume.** The branch you are on may be
   behind `main` (so the running stack deploys stale code — `restart` deploys the
   checked-out tree, see the `waypointctl` skill), or the branch you want to check
   out is locked by another worktree. See `references/branch-and-worktree.md`.

## Routing

- Before any commit — verify the staged diff is complete and self-consistent:
  `references/before-committing.md`.
- Before branching, switching, or restarting — confirm the checkout is current
  and handle sibling-worktree locks: `references/branch-and-worktree.md`.

## Guardrails

- **Inspect the staged diff before committing** (`git diff --cached`); confirm
  every symbol/class/file the change references is defined in the same change.
  Do not assume earlier edits are still present.
- **Investigate stashes before dropping them** — a stash may hold the other half
  of your change. Never `git stash drop`/`clear` on a multi-session host without
  reading the stash first.
- **Prefer moving an existing branch forward** over spinning up a new branch;
  fewer branches means fewer collisions with other sessions.
- **Ground every claim about tree state in git output**, not memory of what you
  did earlier — the tree may have changed since.
