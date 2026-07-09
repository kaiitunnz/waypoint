# Shepherd Reference

The Shepherd is the long-lived lead. It holds global context, owns the board,
selects parents, and decides which search shape to run next. Search agents hold
local context and produce evaluated commits.

## Research Channel

Use one durable channel:

```bash
research:<slug>
```

The slug should be short, lowercase, and stable for the run, for example
`research:fast-decoder` or `research:solver-aime`.

Only the Shepherd writes keyed cells. Search agents post keyless log entries and
direct replies. Keyed cells are tied to the author session and are pruned if that
session is deleted; keep the Shepherd session alive for the run, and mirror each
material state update to a keyless log snapshot so a deleted Shepherd can be
reconstructed from the log plus git.

Core cells:

- `problem` - objective, files in scope, evaluator command, score direction,
  constraints, baseline score, and final artifact expected.
- `budget` - total search agents, max parallelism, per-agent evaluation cap,
  wall-clock stop, permission/model choices, and cleanup policy.
- `seed` - base branch/commit and any research metadata path.
- `leaderboard` - current best branches and family map. Keep it concise and
  update after each completed wave.
- `family:<id>` - one approach family: short label, best branch, best commit,
  best score, strengths, weaknesses, and whether to keep exploring.
- `agent:<n>` - immutable contract for one search agent: type, parent(s),
  branch, evaluator, local context, and expected report.
- `status:<n>` - mutable status. Use metadata for `state`,
  `sid`, `branch`, `score`, `commit`, `family`, and `parent`.

States for `status:<n>`:

```text
todo | running | done | blocked | invalid
```

## Research Contract

Before spawning:

1. Identify the baseline branch or commit.
2. Make the evaluator command executable from any search worktree.
3. Define the metric and direction: maximize, minimize, pass/fail, or ranked
   rubric.
4. Define constraints that invalidate a score: tests, hard limits, public API,
   resource budget, style, safety, or benchmark split.
5. Decide the budget and max parallelism.
6. Decide how results will be landed, if at all.

If any of objective, evaluator, score direction, or budget is ambiguous and
cannot be inferred safely, ask the user before spawning agents.

## Branch Lineage

Keep branch names deterministic:

```text
rs/<slug>-r<n>
```

Record each agent's parent branch or parent commit. For a run with metadata,
prefer `.waypoint-research/<slug>/prompt.md` and
`.waypoint-research/<slug>/findings.md` inside each branch. Create `prompt.md`
on the seed branch when the problem statement is too long for a board cell;
create `findings.md` only once a lineage has facts to record. Commit metadata
before spawning worktrees from that parent, or deliver it through board
cells/artifacts instead; uncommitted files in the Shepherd's checkout are not
visible to `--worktree-base` children. The files are lineage-local: a descendant
sees ancestor findings because it starts from the parent branch, but unrelated
branches do not share full context.

The Shepherd may inspect all branches. Search agents may inspect only their own
cwd and the problem/agent cells they were told to read.

## Lifecycle

### 1. Initialize

Create or select a seed branch from the requested base. Add only metadata needed
for the research run unless the evaluator itself must be added. Record the
baseline score in `problem` and `leaderboard`.

### 2. Initial Explorer Population

Spend a meaningful early fraction of the budget on diverse explorers from the
seed branch. Launch in small waves so you can detect repeated ideas before
wasting the full budget. Do not launch optimizers before several explored
families have evidence.

### 3. Map Families

After each wave, classify attempts by approach family. A family is not just a
score; it is a region of the search space. Track weak families too, because a
later synthesis or targeted constraint fix may use their findings.

### 4. Exploit Without Collapsing

When a family looks promising, spawn serial explorers or optimizers from that
parent. Keep more than one family alive unless the budget is nearly exhausted.
A branch that did not improve may still be a useful parent if it tried a new
mechanism or exposed a constraint.

### 5. Break Plateaus

When scores flatten or agents repeat ideas, change the search shape:

- Explorer fan-out from several promising parents.
- Serial explorers from a non-winning but diverse parent.
- Optimizers on a current best to harvest small gains.
- Synthesis from complementary parents.
- Fresh explorers with a short "ideas already tried" warning.

Do not prescribe a specific idea. Provide context such as observed tradeoffs,
failed families, or constraints the best branch still violates.

### 6. Finalize

Select the best valid branch by score and constraints. Re-run the evaluator from
a clean state. If the final branch includes research metadata that should not
ship, create a clean landing branch from the target base and port only the
intended implementation.

## Parent Selection Heuristics

Use parent selection to steer the population, not to micromanage ideas:

- Baseline parent: use for fresh high-level exploration.
- High-score parent: use for exploitation and late optimization.
- Diverse parent: use when it represents a unique family even if its score is
  not best.
- Constraint-fixing parent: use when the family solves a hard constraint another
  high-score family misses.
- Multi-parent setup: use when two branches have complementary mechanisms and a
  clean combined branch can be prepared by the Shepherd.

## Resume

On resume:

1. Read `waypoint board read research:<slug>`.
2. Read the board log for recent reports.
3. Inspect any `running` session ids in `status:<n>` with
   `waypoint sessions show <sid>`.
4. Mark dead workers `blocked` or `invalid`; do not assume their results landed.
5. Verify done agents from git: branch, commit, evaluator output, and score.
6. Continue with the next search shape from the current leaderboard.

The board is the checkpoint while the Shepherd session exists. Keyless board log
snapshots, git branches, commits, and evaluator outputs are the recovery path if
the Shepherd session was deleted and its keyed cells were pruned.
