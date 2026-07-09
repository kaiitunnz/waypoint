# Search Agent Reference

Search agents are not general-purpose workers. Each one receives a branch,
local context, a standard evaluator, and a narrow experiment role. The Shepherd
owns setup and global state.

## Common Rules For Every Search Agent

Tell each search agent:

- Work only in the current cwd and assigned branch.
- Read the local prompt/findings files if present, plus the assigned `agent:<n>`
  board cell.
- Do not inspect other worktrees, branches, refs, commits, or git history.
- Do not read broad board history unless the Shepherd explicitly says to.
- Implement and evaluate one coherent idea at a time.
- Report factual results only: branch, commit, evaluator command, score,
  approach summary, and failure reason if blocked.
- Update lineage-local findings only with facts learned from this attempt.
- Commit according to the repo's conventions, including DCO sign-off when the
  repo requires it.

Include the exact board commands in each handoff because a child session may not
have this skill installed:

```bash
waypoint board read research:<slug> --key agent:<n>
waypoint board post research:<slug> "agent r<n> done -- branch <branch> commit <hash> evaluator '<command>' score <score> direction <maximize|minimize|pass-fail> validity <valid|invalid|blocked> -- <summary>"
```

## Findings File

Prefer this path when the run owns metadata:

```text
.waypoint-research/<slug>/findings.md
```

Keep findings factual and compact:

```markdown
# Findings

## <agent-id> - <short approach>

- Parent: <branch or commit>
- Commit: <hash>
- Evaluator: `<command>`
- Score: <value and direction>
- Validity: valid | invalid | blocked
- Changed: <one or two factual bullets>
- Learned: <one or two factual bullets>
```

Do not include next-step brainstorming in findings. The Shepherd does that on
the board.

## Explorer

Use explorers for fresh high-level attempts. They should not receive the full
global leaderboard. They may receive a short list of already explored ideas to
avoid if the population is repeating itself.

Handoff shape:

```text
[wp-msg from=<shepherd-sid>] You are a waypoint-research explorer.
Research channel: research:<slug>
Agent id: r<n>
Assigned branch: rs/<slug>-r<n>
Parent: <parent-branch-or-commit>
Evaluator: <command>

Work only in your current cwd and assigned branch. Read the local research
prompt/findings files if present, and read your board contract with:
`waypoint board read research:<slug> --key agent:<n>`. Try one fundamentally
distinct high-level idea, implement it, run the evaluator, update findings with
factual results, commit your changes, then post a keyless report with:
`waypoint board post research:<slug> "agent r<n> done -- branch <branch> commit <hash> evaluator '<command>' score <score> direction <maximize|minimize|pass-fail> validity <valid|invalid|blocked> -- <summary>"`.
Do not inspect other branches, worktrees, refs, commits, or broad board history.

Minimal context: <optional non-prescriptive context, such as ideas to avoid or
observed constraints>
```

Explorer loop:

1. Read local problem and findings.
2. Choose one high-level idea.
3. Implement the idea.
4. Run the evaluator.
5. If a targeted fix is obvious, make a small number of bounded iterations.
6. Stop if the idea is fully tested, invalid, or not improving after a few
   attempts.
7. Update findings, commit, report, and go idle.

## Optimizer

Use optimizers for focused refinement of a promising parent. Some harnesses fork
a parent conversation for this role. Waypoint support varies by backend, so use
the strongest available context mode and record it in `agent:<n>`:

- `fork` - backend/session supports a true fork or imported parent thread.
- `resume` - continue the parent session serially on a new branch in its
  existing worktree.
- `findings-only` - start a fresh session from the parent branch with parent
  findings and a short Shepherd summary.

Do not claim optimizer context inheritance unless the actual launch used it.

Handoff shape:

```text
[wp-msg from=<shepherd-sid>] You are a waypoint-research optimizer.
Research channel: research:<slug>
Agent id: r<n>
Assigned branch: rs/<slug>-r<n>
Parent: <parent-branch-or-commit>
Context mode: fork | resume | findings-only
Evaluator: <command>

Work only in your current cwd and assigned branch. Improve the parent solution
with up to <k> focused iterations. For each iteration, make one targeted change,
run the evaluator, and keep only changes that improve the valid score or repair
a required constraint. Update findings factually, commit the final kept state,
then post a keyless report with:
`waypoint board post research:<slug> "agent r<n> done -- branch <branch> commit <hash> evaluator '<command>' score <score> direction <maximize|minimize|pass-fail> validity <valid|invalid|blocked> -- <summary>"`.
Do not inspect unrelated branches, worktrees, refs, commits, or broad board
history.

Minimal context: <optional non-prescriptive context about parent strengths,
weaknesses, or complementary findings>
```

## Synthesizer

Use a synthesizer only after the Shepherd prepares a branch that contains the
intended parent material. The search agent should not perform arbitrary
cross-branch archaeology.

Handoff shape:

```text
[wp-msg from=<shepherd-sid>] You are a waypoint-research synthesizer.
Research channel: research:<slug>
Agent id: r<n>
Assigned branch: rs/<slug>-r<n>
Parents prepared by Shepherd: <parent-a>, <parent-b>[, ...]
Evaluator: <command>

Work only in your current cwd and assigned branch. The branch has already been
prepared with the relevant parent material. Build one coherent combined
solution, run the evaluator, update findings factually, commit the final state,
and report with:
`waypoint board post research:<slug> "agent r<n> done -- branch <branch> commit <hash> evaluator '<command>' score <score> direction <maximize|minimize|pass-fail> validity <valid|invalid|blocked> -- <summary>"`.
Do not inspect unrelated branches, worktrees, refs, commits, or broad board
history.

Minimal context: <observed tradeoffs to consider, without prescribing a design>
```

## Valid Reports

A report is valid only if it includes:

- Agent id.
- Branch.
- Commit hash.
- Evaluator command.
- Score and score direction.
- Validity under constraints.
- Short factual approach summary.

The Shepherd should mark reports without these fields `blocked` or `invalid`
until verified.
