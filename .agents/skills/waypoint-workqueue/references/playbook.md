# Playbook (commands)

The concrete commands behind the crew lifecycle. The board cell shapes and the
worker message are in `references/org-template.md`.

## Lead

Set up the integration branch, then write `plan` and one `task:<n>` per task:

```bash
job=drop-py38; repo=<repo-root>; base=main
git -C "$repo" switch -c "wq/$job" "$base"
# write plan + tasks (see org-template.md)
```

Assign — for each free worker and each `todo` task, give it a worktree and send
it off. **Choose the backend and model for the task**, and place the worker where
its code lives:

```bash
n=3
# Task branch is `wq/$job-t$n`, NOT `wq/$job/task-$n`: a slash makes it a child
# of the integration ref `wq/$job`, and git cannot hold both a branch and a
# directory at that path ("cannot lock ref ... exists").
git -C "$repo" worktree add "$repo/../.wq/$job/task-$n" -b "wq/$job-t$n" "wq/$job"
sid=$(waypoint sessions start \
  --backend codex --model gpt-5-codex \
  --cwd "$repo/../.wq/$job/task-$n" \
  --title "subagent:wq-$job-$n" | jq -r .session.id)
# task:<n> is the immutable contract — never rewritten. Update status:<n> only.
waypoint board set-meta job:$job --key status:$n --meta state=doing --meta assignee=$sid
# then send the worker the fixed message from org-template.md
```

Put the worktree root **outside** the repo (as above, `"$repo/../.wq/..."`)
or add `.wq/` to `.gitignore`. Git does not auto-ignore a nested worktree path,
so an in-tree `.wq/` shows up as untracked in the integration branch.

The per-task `--backend` / `--model` choice is a core reason to use a crew: spend
a cheap, fast model on mechanical tasks and a stronger one where it matters. See
`references/backends.md` for the harness rundown and model-tier heuristic; keep
it a judgement call, not a routing engine.

**Cross-repo jobs:** `repo` need not be fixed. For a task in another repository,
create its worktree in that repo and point `--cwd` there — one job can span
several repos at once. Track which repo a task belongs to in its `task:<n>` cell.

Check and merge **one task at a time** (sequential merges keep every conflict
between a single branch and the integration branch):

```bash
git -C "$repo" switch "wq/$job"
git -C "$repo" merge --no-ff "wq/$job-t$n"
# run the task's check, e.g. uv run pytest pkg/auth
```

- Clean merge and green check → `git -C "$repo" worktree remove "$repo/../.wq/$job/task-$n"`
  and set the task done: `waypoint board set-meta job:$job --key status:$n --meta state=done`.
- Conflict or red → `git -C "$repo" merge --abort`, hand the task back:
  `waypoint board set-meta job:$job --key status:$n --meta state=todo`, and reassign it.

Finish when no task is `todo` or `doing`: run the **full** suite on `wq/$job`,
post a summary to the board, reap the workers (`waypoint-subagents` → cleanup),
and report integrated vs. blocked counts.

## Worker

1. Read the task: `waypoint board read job:<job-id> --key task:<n>`.
2. Do exactly that task in your cwd (an isolated worktree). Commit once the
   task's check passes: `git add -A && git commit -m "task <n>"`.
3. Report: `waypoint board post job:<job-id> "task <n> done — branch wq/<job-id>-t<n>"`.
   If stuck, post `"task <n> blocked — <reason>"` and stop. Never fake success.
4. Go idle. The lead merges, or hands the task back to you.

## Resume

The board is the checkpoint — no separate state file. A lead (re)starting reads
`waypoint board read job:<job-id>` and continues: `done` tasks are merged (skip);
`todo` tasks get assigned; a `doing` task whose worker is gone
(`waypoint sessions show <sid>` → `exited`/`error`) is handed back to `todo`.

### Resume after the lead dies

1. Read the board: `waypoint board read job:<job-id>`.
2. Find the old lead's workers: `waypoint sessions list --spawned-by <old-lead-sid>`.
3. For each `doing` task, check its assigned worker (`waypoint sessions show <sid>`).
   If `exited`/`error`, the worker is orphaned — hand the task back to `todo`:
   `waypoint board set-meta job:<job-id> --key status:<n> --meta state=todo`.
4. Workers still running can be adopted: read their events to verify progress, then
   resume steering them.

> **`waypoint sessions send` may report a transport timeout even when the input
> landed.** Confirm via `waypoint sessions events <sid>` before resending — a
> duplicate message can cause a worker to execute a task twice.
