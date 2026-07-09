# Waypoint Research Playbook

Use this for command shapes. Always confirm exact flags with `waypoint help` on
the installed version when something differs from these examples.

## Preflight

```bash
waypoint sessions list
waypoint backends
waypoint models <backend>
```

Pick a backend/model/permission profile before spawning. Research runs are often
unattended, so do not leave search agents on a permission mode that will stall
at the first edit or command approval.

## Initialize Board State

```bash
job=<slug>
channel=research:$job

waypoint board post "$channel" \
  "Objective: <objective>. Evaluator: <command>. Score: <maximize|minimize> <metric>. Constraints: <constraints>. Baseline: <branch>@<commit> score <score>. Final artifact: <branch|patch|report|PR>." \
  --key problem

waypoint board post "$channel" \
  "Budget: <n> agents, max_parallel=<p>, per_agent_eval_cap=<k>, wall_time=<duration>. Backend/model/permission: <choices>." \
  --key budget

waypoint board post "$channel" \
  "Seed: base=<base-branch-or-commit>, metadata=.waypoint-research/$job, baseline_score=<score>." \
  --key seed

waypoint board post "$channel" \
  "Current best: baseline score <score>. Families: baseline only." \
  --key leaderboard

waypoint board post "$channel" \
  "snapshot init: objective='<objective>'; evaluator='<command>'; score_direction=<maximize|minimize>; constraints='<constraints>'; baseline=<branch>@<commit> score=<score>; budget='<n> agents max_parallel=<p> eval_cap=<k> wall_time=<duration>'; seed=<base-branch-or-commit>; leaderboard='baseline score <score>'"
```

If you create `.waypoint-research/$job/prompt.md` or other seed metadata for
workers to read locally, commit it on the parent branch before spawning any
`--worktree-base` children. Otherwise use board cells or uploaded artifacts
instead of local metadata.

```bash
git -C "$repo" add ".waypoint-research/$job/prompt.md"
# Use the host repo's required commit convention; in this repo every commit uses -s.
git -C "$repo" commit -s -m "Seed research metadata for $job"
```

For each planned agent:

```bash
n=<agent-number>
branch=rs/$job-r$n

waypoint board post "$channel" \
  "type=<explorer|optimizer|synthesizer>; parent=<parent>; branch=$branch; evaluator=<command>; context=<minimal context>; report required: branch, commit, command, score, validity, summary." \
  --key agent:$n

waypoint board post "$channel" "agent $n status" \
  --key status:$n --meta state=todo --meta branch=$branch --meta parent=<parent>

waypoint board post "$channel" \
  "snapshot agent=$n state=todo type=<explorer|optimizer|synthesizer> parent=<parent> branch=$branch evaluator='<command>' context='<minimal context>'"
```

## Prepare A Search Branch

For a normal explorer or findings-only optimizer, let Waypoint create the branch
and worktree from the parent branch:

```bash
session_scope=(--tag "research=$job")
if [[ -n "${WAYPOINT_SESSION_ID:-}" ]]; then
  session_scope+=(--spawner-session-id "$WAYPOINT_SESSION_ID")
fi

sid=$(waypoint sessions start \
  --backend <agent> --model <model-id> --permission-mode <auto-approving-mode> \
  --cwd "$repo" --worktree "$branch" --worktree-base "<parent-branch>" \
  --title "subagent:research-$job-r$n" "${session_scope[@]}" \
  | jq -r .session.id)

waypoint board set-meta "$channel" --key status:$n \
  --meta state=running --meta sid=$sid --meta branch=$branch --merge
waypoint board post "$channel" \
  "snapshot agent=$n state=running sid=$sid branch=$branch parent=<parent>"
```

When `WAYPOINT_SESSION_ID` is unset, the Shepherd is not running inside a
Waypoint session. In that case do not pass `--spawner-session-id`; the
`research=$job` tag is the cleanup/listing scope. When it is set, keep both the
tag and spawner id so permission inheritance and owner-scoped inspection still
work.

Confirm the session did not die on turn 1:

```bash
waypoint sessions show "$sid"
```

Then send the handoff prompt from `search-agents.md`.

## Optimizer Context Modes

Use one of these, in order of fidelity:

1. **fork** - if `waypoint help` and the backend expose a true session/thread
   fork or import for the parent agent, launch with that mechanism and record
   `context=fork`.
2. **resume** - continue the parent search session serially. Instruct it to make
   a new branch from the parent branch inside its existing worktree, then run
   the optimizer handoff. Record `context=resume`.
3. **findings-only** - start a fresh session with `--worktree` from the parent
   branch and include only parent findings plus a short Shepherd summary.
   Record `context=findings-only`.

Do not invent a fork flag. Check the installed CLI and backend.

## Multi-parent Setup

A synthesizer should receive an already prepared branch. The Shepherd prepares
it, resolves setup conflicts, and commits the setup before starting the agent.

Preferred pattern:

```bash
primary=<parent-a>
secondary=<parent-b>
branch=rs/$job-r$n
scratch=$(mktemp -d)
wt="$scratch/rs-$job-r$n"
git -C "$repo" worktree add -b "$branch" "$wt" "$primary"
git -C "$wt" merge --no-commit "$secondary"
# Resolve only setup conflicts needed to make a coherent starting point.
git -C "$wt" add -A
# Use the host repo's required commit convention; in this repo every commit uses -s.
git -C "$wt" commit -s -m "Prepare synthesis $branch"
waypoint board post "$channel" "manual synthesis worktree agent=$n path=$wt branch=$branch"
```

Then launch the Waypoint session in that prepared worktree with `--cwd` pointing
at the prepared path and the same `session_scope` used for ordinary search
agents, so tag-based listing/reap covers synthesizers too:

```bash
sid=$(waypoint sessions start \
  --backend <agent> --model <model-id> --permission-mode <auto-approving-mode> \
  --cwd "$wt" --title "subagent:research-$job-r$n" "${session_scope[@]}" \
  | jq -r .session.id)
waypoint board set-meta "$channel" --key status:$n \
  --meta state=running --meta sid=$sid --meta branch=$branch --merge
```

If branch/worktree setup cannot be made coherent, do not spawn the synthesizer;
mark the planned agent blocked. Manual synthesis worktrees created this way are
not recorded as Waypoint `session.worktree_path`; `sessions reap` will not
remove them.

## Wait And Collect

Wait for active search sessions:

```bash
waypoint sessions wait <sid> <sid> ...
```

Read logs and verify from git:

```bash
waypoint board log "$channel"
waypoint sessions events <sid>
git -C <agent-worktree> status --short
git -C <agent-worktree> rev-parse HEAD
```

For every completed agent:

1. Re-run or inspect the exact evaluator output.
2. Confirm the commit hash.
3. Inspect the diff enough to understand the family.
4. Update `status:<n>` metadata with `state`, `score`, `commit`, and `family`.
5. Update `family:<id>` and `leaderboard`.

Example:

```bash
waypoint board set-meta "$channel" --key status:$n \
  --meta state=done --meta score=<score> --meta commit=<hash> --meta family=<id> --merge
waypoint board post "$channel" \
  "snapshot agent=$n state=done branch=$branch commit=<hash> evaluator='<command>' score=<score> direction=<maximize|minimize|pass-fail> validity=<valid|invalid|blocked> family=<id>; leaderboard='<short current best>'"
```

## Continue Or Stop

Continue when:

- Budget remains.
- Families are still diverse.
- A plateau suggests a new search shape.
- A promising parent has not received enough serial development.

Stop when:

- Budget or wall time is exhausted.
- The best valid score has saturated and new agents repeat old families.
- The evaluator or constraints are unreliable.
- The user asks for a report or landing branch.

## Cleanup

Do not reap useful live sessions until the run is truly finished or you have
uploaded/preserved any artifacts the user needs. Default to reaping without
branch pruning so candidate branches survive:

```bash
waypoint sessions reap --tag "research=$job"
```

Use `--prune-branches` only after the best branch and any runner-up branches have
been copied or renamed outside the spawned branch set, and every remaining
spawned branch is disposable.

For manual synthesis worktrees, remove the worktree explicitly after the session
is idle and the branch has either been preserved or declared disposable:

```bash
git -C "$repo" worktree remove "$wt"
git -C "$repo" branch -d "$branch"   # only if the branch is disposable and merged
```
