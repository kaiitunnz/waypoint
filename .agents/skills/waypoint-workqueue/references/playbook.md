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
lead=$WAYPOINT_SESSION_ID   # the lead's own session id; set --spawner-session-id explicitly if unset
# `--worktree` creates branch `wq/$job-t$n` off the integration branch in a
# sibling worktree, records the path on the session for cleanup, and launches
# the worker there — no manual `git worktree add`. `--cwd` is the repo root the
# worktree is cut from.
sid=$(waypoint sessions start \
  --backend codex --model gpt-5-codex \
  --cwd "$repo" --worktree "wq/$job-t$n" --worktree-base "wq/$job" \
  --title "subagent:wq-$job-$n" --spawner-session-id "$lead" \
  | jq -r .session.id)
# task:<n> is the immutable contract — never rewritten. Update status:<n> only.
waypoint board set-meta job:$job --key status:$n --meta state=doing --meta assignee=$sid
# then send the worker the fixed message from org-template.md
```

`--worktree` removes the whole class of plumbing this step used to need: it picks
a sibling path outside the working tree (so nothing shows up as untracked) and
names the branch for you, so the old `wq/$job/task-$n` slash-collision and the
in-tree `.wq/` gitignore caveats no longer apply. `--spawner-session-id "$lead"`
is what makes owner-scoped reap work at the end (`sessions reap --spawned-by
"$lead"`); it defaults from `WAYPOINT_SESSION_ID` when the lead is a session, but
set it explicitly so cleanup is reliable.

The per-task `--backend` / `--model` choice is a core reason to use a crew: spend
a cheap, fast model on mechanical tasks and a stronger one where it matters. See
`references/backends.md` for the harness rundown and model-tier heuristic; keep
it a judgement call, not a routing engine.

**Cross-repo jobs:** `repo` need not be fixed. For a task in another repository,
point `--cwd` at that repo's root (with `--worktree`, the sibling worktree is cut
there) — one job can span several repos at once. Track which repo a task belongs
to in its `task:<n>` cell.

**Monitor without polling.** Once workers are assigned, block on them rather than
looping: `waypoint sessions wait <sid> <sid> …` returns when every worker reaches
idle/terminal — that is the default; pass `--any` to return on the first. To react
to a blocked child instantly, follow the fleet:
`waypoint sessions events --follow --spawned-by "$lead" --filter approval_request`
surfaces matching events across all your workers as they happen (`--filter` takes a
single event kind; drop it to see everything).

Check and merge **one task at a time** (sequential merges keep every conflict
between a single branch and the integration branch):

```bash
git -C "$repo" switch "wq/$job"
git -C "$repo" merge --no-ff "wq/$job-t$n"
# run the task's check, e.g. uv run pytest pkg/auth
```

- Clean merge and green check → set the task done: `waypoint board set-meta
  job:$job --key status:$n --meta state=done`. The worktree is removed for you
  when the worker is reaped (its path is recorded on the session), so there is no
  manual `git worktree remove`.
- Red check → `git -C "$repo" merge --abort`, hand the task back
  (`--meta state=todo`), and reassign it (often a fresh worktree).
- Conflict → tell the two kinds apart. An **additive** conflict — two workers
  appending to the same append-only file (a test suite, a registry, an export
  list) — is not a real disagreement: keep both. Take the integration side and
  re-append the worker's block: `git checkout --ours <file>`, then add the
  worker's additions to the end (or hand-merge both hunks) and commit. A
  **semantic** conflict — both branches changed the same logic — is the one you
  `merge --abort` and hand back. If a shared file conflicts on *every* merge, the
  tasks were not independent enough and should have been one worker.

Finish when no task is `todo` or `doing`: run the **full** suite on `wq/$job`,
then — for a server/CLI/integration artifact — **exercise the real thing**, not
just unit tests. Green mocked tests routinely miss wiring bugs (a 422 from an
unset field, git chatter polluting stdout); start the app and run the new paths
once. Post a summary to the board, reap the workers with `waypoint sessions reap
--spawned-by "$lead"` (this also removes their recorded worktrees), and report
integrated vs. blocked counts.

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

Read the job's history with `board log job:<job-id>`; confirm what landed from
git (ground truth for code), the log is the narrative.

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
