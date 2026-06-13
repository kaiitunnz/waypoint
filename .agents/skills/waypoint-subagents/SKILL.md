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

## Before anything: preflight

The `waypoint` CLI is not guaranteed to be reachable from inside every session.
Confirm it before relying on it — see `references/preflight.md`. If it is
unavailable, stop and report; do not guess.

## Ownership convention

Waypoint has no parent/owner field on sessions, so a child you spawn is
otherwise indistinguishable from a user's own session. Establish ownership by
convention:

- Title every child you create with `--title "subagent:<short-purpose>"`.
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
