# Git and integration

Two guarantees, always on: **per-ticket worktree isolation** (no two sessions ever
share a working tree) and a **serialized single-integrator gate** (trunk advances
only through the manager, behind a lease). Conflicts can surface only at that gate,
never as a corrupted tree. Footprint-based conflict-aware scheduling is an
optimization layered on top — deferrable — not the guarantee.

## Per-ticket worktree isolation

Every ticket executes on its own branch in its own worktree, spawned when the
manager delegates:

```bash
sid=$(waypoint sessions start \
  --preset lead-opus-1m \
  --cwd <repo-root> \
  --worktree ticket/<id> --worktree-base {{trunk}} \
  --title "subagent:ticket-<id>:tech-lead" \
  --spawner-session-id "$WAYPOINT_SESSION_ID" \
  | jq -r .session.id)
```

- `--worktree ticket/<id>` names the **branch**; the runtime force-derives the
  worktree path as a **repo sibling** (`<repo>-ticket-<id>`), outside the tree, so
  nothing shows up as untracked in the main checkout. Capture the returned session
  id and the derived path (`waypoint sessions show "$sid"`) and record them on the
  ticket (`ticket transition … --lead-session-id "$sid" --worktree-path <path>`).
- `--worktree-base {{trunk}}` cuts the branch from trunk.
- `--spawner-session-id` makes the lead owner-scoped so the manager can list and
  reap only its own subtree; the `subagent:ticket-<id>:tech-lead` title is the
  spawn dedup key reconcile matches on.

**Recursive isolation.** A tech-lead that fans its ticket out (via
`waypoint-workqueue` or `waypoint-crew`) gives its workers **sub-worktrees off the
ticket branch** and integrates them locally into **one commit ref** before
reporting up. The manager only ever sees the single ticket branch — never a
worker's tree. No session shares a working tree at any level.

## Spawn dedup and branch collisions

`git worktree add -b <branch>` **fails if the branch already exists**, so there are
two distinct spawn paths, and reconcile (`references/loop.md`) picks between them:

- **Live same-title session exists** → **adopt** it; do not spawn.
- **Initial delegate, stale `ticket/<id>` branch from an incomplete reap** → the
  branch has **no committed work**, so delete it and re-create:
  `git -C <repo-root> branch -D ticket/<id>` (or reap the stale session with
  `--prune-branches`), then spawn with `--worktree` as above.
- **Lead-died resume, work on the branch** → the branch **holds committed work**,
  so it must survive. See the next section.

## Terminate-not-delete resume

A dead lead in a live-lead state is recovered **without losing its branch**. The
key distinction:

- `waypoint sessions terminate <sid>` stops the process but **keeps the record and
  the worktree** — the branch and its commits survive.
- `waypoint sessions delete <sid>` removes the record **and the worktree** — only
  use this after integration, when the work has landed on trunk.

So a lead-died resume terminates (never deletes) the dead session, then spawns a
fresh lead **onto the preserved worktree** — reusing the existing branch, with
**no `--worktree` flag** (which would try to re-create the branch with `-b` and
fail):

```bash
waypoint sessions terminate <dead-lead-sid>          # preserve the branch + worktree
waypoint manager ticket transition <id> --to <same-state> \
  --lead-session-id <new-sid> --reason lead-died      # self-loop: consumes lead_restarts
new=$(waypoint sessions start --preset lead-opus-1m \
  --cwd <preserved-worktree-path> \
  --title "subagent:ticket-<id>:tech-lead" \
  --spawner-session-id "$WAYPOINT_SESSION_ID" | jq -r .session.id)
waypoint sessions wake-on-board "$new" --channels ticket-<id> --wake-on-inbox
```

The fresh lead re-reads the durable `ticket-<id>` log — the `status` cell and every
owed relay — and drives the normal transitions from there, so committed work **and**
a human answer given while the old lead was alive are both preserved
(`references/loop.md`). Past `max_lead_restarts` the self-loop is rejected;
escalate with `--to blocked`.

**Integrate-then-delete ordering is mandatory.** Only `delete` removes a
`--worktree` session's worktree; `terminate` preserves it for a resume. So the reap
of a merged ticket's subtree happens **after** integration:
`waypoint sessions reap --spawned-by "$WAYPOINT_SESSION_ID" --recursive` (scoped to
the ticket's subtree), optionally `--prune-branches` once the branch has landed.

## The serialized integration lease

Trunk is advanced by the **manager alone** — in both `pr` and `local` modes —
behind the `integration` lease, so two merges never race and the working tree is
never contended:

```bash
waypoint manager lock acquire --owner "$WAYPOINT_SESSION_ID"   # --ttl-seconds defaults to the manifest
# … rebase/update onto trunk → verify / CI → merge …
waypoint manager lock release --owner "$WAYPOINT_SESSION_ID"
```

- There is a single implicit `integration` lease (no lease-name argument); `acquire`
  fails `409` if another live owner holds it.
- **Release on *every* exit from `merging`** — `merged`, `deferred`, `revising`
  (conflict), or `blocked` (CI red) — not just the happy path. A ticket that hits a
  conflict releases the lease and leaves `merging` *before* it waits for a compute
  slot, so a stuck merge never strands the gate.
- **Crash recovery.** A manager that dies holding the lease is recovered by
  `waypoint manager lock steal --owner "$WAYPOINT_SESSION_ID"`, which succeeds
  **only after the TTL expires** (a liveness backstop). On restart, a `merging`
  ticket is reconciled against `gh pr view` before any re-attempt, so a
  mid-merge crash never double-merges.

## PR-based integration and human-review-until-merge

With `integration.mode: pr`, the manager opens a PR for the ticket branch and the
**human is the sole merge authority** — autonomy runs up to the PR, never through
it. The manager does not invoke a personal `/create-pr` skill; it opens the PR
inline (the tech-lead template `templates/tech-lead/report.md` inlines the exact
`gh pr create` prose). The manager then:

1. Posts the PR to the human as an inbox **approval** item and moves the ticket to
   `review_requested` (the slot frees; the lead parks alive).
2. On the human's answer (relayed via the durable log): **request-changes** →
   `revising` (relay the feedback to the lead); **merge** → acquire the lease,
   move to `merging`, land the PR; **abort / latency-timeout** → `abandoned`.
3. Re-posts `done` on each new PR head while `revising`, looping review-until-merge
   until the human merges or aborts.

**Rebasing** onto an advanced trunk before landing (and resolving only trivial
lockfile/generated conflicts, bouncing semantic ones to `revising`) is inlined in
`templates/tech-lead/address-review.md` and the manager's
`templates/manager/integrate.md` — written out as `git rebase` prose, never a
`/rebase-main` personal-skill call. A **partial** completion spawns follow-up
tickets for the unmet goals **only at the `merging → deferred` edge**, once the
delivered subset has actually merged, with a deterministic dedup key so a re-run
does not double-create them.
