---
name: waypoint-research
description: Use when a coding agent needs to run open-ended research, algorithm discovery, benchmark optimization, or exploratory engineering over Waypoint infrastructure using a shepherd plus isolated search agents. A shepherd steers explorer and optimizer sessions across separate git branches/worktrees, tracks evaluated scores and lineages on the Waypoint board, and adaptively balances exploration and exploitation. Not for ordinary product lifecycle work (use waypoint-crew), independent fixed task batches (use waypoint-workqueue), or one coherent code change (use delegate-and-review via waypoint-subagents).
---

# Waypoint Research

Run an open-ended discovery loop on Waypoint: one
**Shepherd** owns global strategy and budget, while many **Search Agents** run
isolated experiments in their own git branches/worktrees. The goal is not to
split a known implementation plan. The goal is to discover better approaches
when the solution space is large, the best family of ideas is unknown, and a
standard evaluator can score attempts.

This skill uses Waypoint-native mechanics: Waypoint launches and owns sessions,
`waypoint board` carries durable global state, and
`waypoint sessions start --worktree` creates isolated program states. Do not use
backend-specific spawn commands from an external harness directly.

## Fit

Use this when the user asks to optimize a benchmark, improve an algorithm, find
a stronger prompt or solver, run autoresearch-style exploration, or search for a
better implementation family over hours or many agents.

Skip it when the target is a product shipped through roles and checkpoints
(`waypoint-crew`), a known list of independent tasks (`waypoint-workqueue`), or
a single coupled implementation change (`waypoint-subagents` delegate-and-review).

## Foundation

Skim these first:

- `waypoint-subagents` for spawning, steering, waiting on, and reaping sessions.
- `waypoint-comms` for board cells and direct sends.
- `waypoint-worktree` before any branch, commit, cleanup, or PR action.
- `waypoint-workqueue/references/playbook.md` for current `sessions start
  --worktree`, model, permission, wait, and cleanup mechanics.

Then read the Waypoint Research references in order:

1. `references/shepherd.md` - roles, board state, lifecycle, strategy, resume.
2. `references/search-agents.md` - explorer, optimizer, synthesis, findings, and
   handoff prompts.
3. `references/waypoint-playbook.md` - concrete Waypoint command shapes.

## Core Model

- **Shepherd** - you. Owns the research channel, budget, evaluator definition,
  parent selection, branch/worktree setup, agent prompts, leaderboard, and final
  recommendation. Search agents may report facts; only the shepherd writes keyed
  board cells.
- **Explorer Search Agent** - fresh local context. Starts from a baseline or
  parent branch and tries one high-level idea. Use explorers early and on
  plateaus to keep idea families diverse.
- **Optimizer Search Agent** - context-bearing refinement. Continues a promising
  parent with a few focused iterations. Use optimizers only after some families
  have evidence.
- **Synthesizer Search Agent** - optional multi-parent branch. Combines or
  compares compatible findings when two families have complementary strengths.

## Workflow

1. **Define the research contract.** Establish the objective, evaluator command,
   metric direction, constraints, baseline branch/commit, budget, parallelism
   limit, and final artifact. If no credible evaluator exists, first create one
   or ask the user for the scoring rule.
2. **Create durable state.** Open `research:<slug>` on the board and write
   `problem`, `budget`, `seed`, `leaderboard`, and per-agent `agent:<n>` /
   `status:<n>` cells as described in `references/shepherd.md`.
3. **Seed diverse exploration.** Launch a small initial wave of fresh explorers
   from the baseline. Give each minimal context and no prescribed idea. Wait for
   evaluated commits and factual summaries.
4. **Update the map.** Read branch diffs, evaluator outputs, commits, and
   findings. Cluster attempts into families, update the leaderboard, and decide
   where search budget should go next.
5. **Adapt search shape.** Interleave explorer fan-out, serial explorers,
   optimizers, and synthesizers. Keep multiple families alive; do not collapse
   all effort onto the current best unless the budget is ending.
6. **Stop on budget or saturation.** When budget is spent, time expires, scores
   plateau, or the user asks to stop, verify the best branch independently and
   report the best commit, score, approach family, residual risks, and next
   experiments.
7. **Land only on request.** A research run produces candidate branches. Do not
   merge all branches. If the user wants a PR, make a clean final branch from
   the target base and carry over only the winning implementation and intended
   provenance.

## Guardrails

- Require a real budget before spawning a broad run: number of agents, max
  parallelism, wall time, or evaluation cap. Ask when the user's request implies
  substantial compute or unattended spend.
- Keep explorers fresh. Their value is local context and independent program
  state; do not feed them the shepherd's full global history.
- Keep prompts minimal and non-prescriptive. Tell agents what has already been
  explored or what tradeoffs were observed; do not tell them the exact idea to
  implement.
- Keep program states isolated. Every major experiment gets a separate branch
  and worktree. Search agents must not inspect other worktrees, branches, refs,
  or git history unless the shepherd explicitly made a synthesis branch for
  them.
- Ground every score in the standard evaluator. Do not accept a search agent's
  self-report without the command, score, branch, and commit hash.
- Treat research branches as a population, not a work queue integration branch.
  Preserve useful losers for later parent selection; merge only a deliberately
  selected final result.
- Keep the Shepherd session alive while keyed board cells are the active run
  state. Keyed cells are pruned when their author session is deleted, so mirror
  important state to keyless log snapshots before any teardown.
- Respect the host repo's commit and security rules, including DCO sign-off and
  secret handling when applicable.
