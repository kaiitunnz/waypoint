---
name: waypoint-subagents
description: Use when a coding agent inside a Waypoint session needs to spawn and manage child Waypoint sessions as subagents — fanning work out to other backends/models, running user-visible parallel sessions, or delegating a durable task that outlives the current turn. Distinct from your own harness's in-process subagents.
---

# Waypoint Subagents

Spawn and drive **child Waypoint sessions** through the `waypoint sessions`
CLI, treating them as subagents. Each child is a full, independent Waypoint
session: it shows up in the UI, the user can steer it, it has its own
transcript, and it survives your own turn ending.

## When to use this vs. your harness's own subagents

Prefer your harness's built-in subagents (Claude Code's Task tool, Codex's
equivalent) for ordinary in-context fan-out — they are cheaper, in-process, and
need no preflight.

Reach for Waypoint sub-sessions only when you need something they cannot give:

- A **different backend or model** than your own (e.g. a Claude session
  delegating a task to a Codex session).
- Work the **user can watch and steer** in the Waypoint UI while it runs.
- A **durable** session that must outlive your current turn.

If none of those apply, do not spawn a Waypoint session.

## Two shapes: parallel crew vs. delegate-and-review

Spawning children comes in two shapes, good for opposite kinds of work:

- **Parallel crew** — many children, each an independent task, merged by a lead.
  For batch work too big for one context: migrations, codemods, per-file sweeps.
  See `waypoint-workqueue`.
- **Delegate-and-review** — *one* child takes a whole coherent chunk and you
  review its diff before integrating. For work too large to do inline but too
  tightly coupled to parallelize (a multi-file change to one module, where
  splitting it would only create merge conflicts). You stay the reviewer: read
  the diff, run the checks yourself, own the merge. This is the right move for
  exactly the coupled work the crew is wrong for.

## Before anything: preflight

The `waypoint` CLI is not guaranteed to be reachable from inside every session.
Confirm it before relying on it — see `references/preflight.md`. If it is
unavailable, stop and report; do not guess.

## Ownership convention

Every session you spawn carries a `spawner_session_id` set at creation — this is
the structural ownership link. It drives permission inheritance and lets you list
or reap only your children:

```bash
waypoint sessions list --spawned-by <your-sid>   # list children you own
waypoint sessions list --mine                     # shorthand: children of the current session
waypoint sessions reap --spawned-by <your-sid>    # reap only your children
waypoint sessions reap --mine                     # shorthand
```

Reinforce ownership with the title convention — title every child you create with
`--title "subagent:<short-purpose>"` — because `spawner_session_id` may not be
visible to a downstream lead reading the board.

- Keep the returned session ids in your working state for the rest of the turn.
- **You own the sessions you spawn, and only those.** You may steer, read, and
  terminate them freely. Never terminate, interrupt, or send to a session you
  did not create without explicit user confirmation, and never the personal
  assistant (the server rejects that anyway).

## Routing

- Verify the CLI is reachable and authenticated: `references/preflight.md`.
- Spawn children and wait for them to finish: `references/spawn-and-poll.md`.
- Decide how a child handles approvals, and service its approvals and questions:
  `references/permissions.md`.
- Read their output, continue them, or service their approvals and questions:
  `references/collect-and-steer.md`.
- Tear down the children you spawned: `references/cleanup.md`.

## Guardrails

- Preflight first; degrade gracefully (report, don't fabricate) if the CLI is
  missing or unauthenticated.
- Keep fan-out small and deliberate — there is no server-side concurrency
  limit, so a runaway loop will exhaust host resources.
- Reap what you spawn. Leaving orphaned `subagent:*` sessions behind is a bug.
- Ground every status claim in `waypoint sessions show`/`events` output, not
  assumption.
