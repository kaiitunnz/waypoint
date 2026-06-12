---
name: waypoint-workqueue
description: Use when a coding agent needs to run a batch of independent software tasks across a crew of durable Waypoint sessions — possibly different models or backends, in separate worktrees or even separate repos — for a migration, codemod, audit, or test port too large for one context window. A lead splits the work, workers each do one task and report, and the lead checks and merges. Comes with a ready-made crew template to adapt. Not for tightly-coupled feature work or jobs that fit one in-process run.
---

# Waypoint Work Queue

Run a batch of **independent** tasks across a **crew** of Waypoint sessions,
coordinated on one shared board channel. A **lead** splits the job into tasks,
hands each to a **worker** that does it in an isolated git worktree, then checks
the result and merges it. The job lives on the board, so it survives the lead
running out of context and can be resumed.

Builds on two skills — skim them first: `waypoint-subagents` (spawn and reap
workers) and `waypoint-comms` (the board and direct sends).

## Why a crew, not in-process fan-out

A Waypoint crew can do two things your harness's own subagents cannot — and they
are the whole reason to reach for this:

- **Mix models and harnesses.** Each worker is its own session on any backend
  (`claude_code`, `codex`, `opencode`, `tmux`), so the crew can be heterogeneous:
  a cheap, fast model on the mechanical tasks and a stronger one on the hard
  ones, or whichever harness fits a given task. Pick `--backend` / `--model` per
  worker.
- **Span worktrees and workspaces.** Each worker runs in its own git worktree —
  or a different repo entirely. One job can sweep across many isolated trees and
  several repositories at once, free of a single harness's one-workspace limit.

If you need neither, you do not need this skill — use in-process fan-out.

## Use it when

The work is independent, repetitive, and bigger than one context window — a
repo-wide (or multi-repo) migration or codemod, a per-file audit or fix sweep,
porting a test suite file by file.

## Skip it when

- The tasks depend on each other or share a moving interface — one agent handles
  coupled work better than a crew.
- It fits a single in-process run (your harness's own Task/Workflow fan-out) and
  needs no separate model, harness, or workspace — that is cheaper.
- It is only a few tasks — just do them yourself; the crew has overhead.

## How it works

Start from the **crew template** in `references/org-template.md` instead of a
blank page — it gives you roles, one board channel, and two kinds of cell that
work out of the box. Treat it as a launch point: adapt the worker count,
backends, and repos to the job, and reshape the structure where the work calls
for it. Run the commands in `references/playbook.md`, and pick each worker's
harness and model with `references/backends.md`.

## Guardrails

- Size the crew to the work and scale it deliberately — fan-out has no
  server-side limit, so an unbounded pool exhausts the host. Reap what you finish
  with.
- The lead owns the board cells; workers only report. A worker-authored cell is
  pruned when the worker is reaped, so durable state must stay with the lead.
- Give each worker a task it can do with no other context, and its own worktree —
  never let two workers share a tree.
- Check the **final merged** result, not just per-task success.
