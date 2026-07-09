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
it off. Pre-spawn checklist, all settled **before** the `sessions start`:

- **Preset (preferred).** Run `waypoint presets list` first. If the user has a
  worker preset for this crew (or a suitable default), spawn with `--preset <id>`
  and let it supply backend/model/effort/permission — you still pass the
  per-worker `--cwd` / `--worktree` / `--title` / `--spawner-session-id`, and any
  explicit flag overrides the preset. Confirm the preset's model and permission
  posture (`waypoint presets show <id>`) satisfy the two checks below; don't
  assume a preset auto-approves. With no fitting preset, set the fields
  explicitly as follows.
- **Model.** Run `waypoint models <backend>` and pass an id **verbatim** — never
  guess from memory (a wrong id spawns fine and only dies on turn 1). Unsure
  which tier fits? Ask the user. `sessions start` warns if the id isn't in the
  backend's catalogue, but treat that as a backstop, not the check.
- **Permission mode.** A crew runs unattended, so the workers must auto-approve —
  do **not** leave them on the inherited `default` (they stall silently on the
  first approval). Pick an auto-approving `--permission-mode` for the backend
  (`waypoint backends` lists the ids); if you can't determine a safe one, ask the
  user rather than spawning blind.
- **Placement.** `--cwd` is the repo root the worktree is cut from; place the
  worker where its code lives.
- **Transport (optional).** `--backend` is the agent; `--transport` picks the
  interface. Omit it to take the agent's `default_transport` — a `claude_code`
  worker then runs over the Emulated (`claude_tty`) transport, where model /
  permission swaps relaunch the pane rather than applying inline. Pass
  `--transport <id>` when a task needs a specific interface; `claude_tty` /
  `tmux` are transport ids, not `--backend` values.

```bash
n=3
lead=$WAYPOINT_SESSION_ID   # the lead's own session id; set --spawner-session-id explicitly if unset
# `--worktree` creates branch `wq/$job-t$n` off the integration branch in a
# sibling worktree, records the path on the session for cleanup, and launches
# the worker there — no manual `git worktree add`.
sid=$(waypoint sessions start \
  --backend <agent> --model <model-id> --permission-mode <auto-approving-mode> \
  --cwd "$repo" --worktree "wq/$job-t$n" --worktree-base "wq/$job" \
  --title "subagent:wq-$job-$n" --spawner-session-id "$lead" \
  | jq -r .session.id)
# task:<n> is the immutable contract — never rewritten. Update status:<n> only.
waypoint board set-meta job:$job --key status:$n --meta state=doing --meta assignee=$sid
# then send the worker the fixed message from org-template.md
# Confirm the worker actually started its turn before treating it as assigned —
# a green `start` is not proof the model/mode were accepted:
waypoint sessions show "$sid"   # expect running/working, not exited/error on turn 1
```

A stalled worker isn't a respawn: widen its mode in place with
`waypoint sessions set-permission-mode <sid> <mode>`.

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

Integrate **one task at a time**, and keep the integration branch's history
**linear** — no merge commits. Rebase each task's branch onto the current
integration tip, check it there, then fast-forward. Because the worker stays
**alive** on `wq/$job-t$n` in its own worktree, that branch is already checked out
there — so **rebase it in the worker's worktree, not in `$repo`** (a `git switch
wq/$job-t$n` in `$repo` would fail with `already used by worktree`). The final
`merge --ff-only` is safe from `$repo` because a fast-forward does not check out
the source branch:

```bash
# worker's worktree path — from `git worktree list` or `waypoint sessions show <sid>`
wt=<worker-worktree>
git -C "$wt" rebase "wq/$job"        # replay the task's commits onto the integration tip, where the branch lives
# run the task's check in $wt, e.g. (cd "$wt" && uv run pytest pkg/auth)
git -C "$repo" switch "wq/$job"      # the integration checkout (never a worker's tree)
git -C "$repo" merge --ff-only "wq/$job-t$n"   # green check → fast-forward, never checks out the source
```

A linear integration branch is the preferred shape. Rebasing one branch at a
time keeps every conflict between a single task and the integration tip, and the
branch lands cleanly however the final PR is merged. Merge commits are the thing
to avoid: a squash- or rebase-style landing flattens them and re-replays the
underlying commits, so any conflict you resolved *inside* a merge commit
silently resurfaces at landing time.

- Green check → fast-forward (above) and set the task done: `waypoint board
  set-meta job:$job --key status:$n --meta state=done`. If you reuse the worker
  (below), it stays in its worktree and starts its next task on a fresh branch
  there; the worktree is only removed when you finally reap the worker.
- Red check → hand the task back (`--meta state=todo`) and reassign it — to the
  **same parked worker**, which resets **in its existing worktree**, not a respawn.
  Since the retry keeps the same task number, reset the existing branch with
  `git switch -C wq/$job-t<n> "wq/$job"` (capital `-C` — plain `-c` fails, the
  branch already exists from the failed attempt). You checked *before* the
  fast-forward, so the integration branch is untouched — nothing to undo.
- Conflict during the rebase → tell the two kinds apart (resolve it **in `$wt`**,
  where the rebase is running — `git -C "$wt" …` for every step below, not
  `$repo`). An **additive** conflict — two workers appending to the same
  append-only file (a test suite, a registry, an export list) — is not a real
  disagreement: keep both. Take the integration side and re-append the worker's
  block (`git checkout --ours <file>`, then add the worker's additions to the end,
  or hand-merge both hunks), `git add` it, and `git rebase --continue`. A
  **semantic** conflict — both branches changed the same logic — is the one you
  `git rebase --abort` and hand back. If a shared file conflicts on *every* task, the tasks were not
  independent enough and should have been one worker.

**Reusing a worker across tasks.** Prefer parking a free worker (idle **and
alive**) and handing it the next `todo` task over reaping and respawning per task —
you keep its warmed-up context and avoid a later thread reimport. The mechanic:
a session is **pinned to its launch `cwd`** (there is no way to repoint a running
session), so a reused worker **keeps its one worktree and rotates the branch inside
it** — it does *not* move to a new worktree. Send it the next `task:<m>` (its fixed
"work in your cwd" still holds) and have it start on a fresh branch off the current
integration tip: `git switch -c wq/$job-t<m> "wq/$job"` from within its worktree.
This works because worktrees share the object store (the `wq/$job` ref is visible)
and the worker only ever checks out its own task branches — the integration branch
itself stays in your tree, so nothing collides. The worktree is removed only when
you eventually reap the worker; there is nothing to `git worktree remove` between
tasks.

Finish when no task is `todo` or `doing`: run the **full** suite on `wq/$job`,
then — for a server/CLI/integration artifact — **exercise the real thing**, not
just unit tests. Green mocked tests routinely miss wiring bugs (a 422 from an
unset field, git chatter polluting stdout); start the app and run the new paths
once. Post a summary to the board, then reap the workers with `waypoint sessions
reap --spawned-by "$lead" --prune-branches` (this removes their recorded worktrees
*and* force-deletes the leftover `wq/$job-t<n>` branches; without
`--prune-branches`, an unmerged worker branch survives and collides with a later
respawn's `--worktree`), and report integrated vs. blocked counts. **If the PR may
still need review-fix iteration, hold the crew** (or a subset) until it lands
rather than reaping now — a later fix continues a parked worker in place, where a
reaped one would have to reimport the thread.

A worker's cwd files are user-readable by opening its session, so don't
bulk-upload them; but reaping removes the worktree, so `sessions upload --pin`
(the `waypoint` skill's `references/artifacts.md`) anything the user must keep
that isn't already landed on `wq/$job` or in a durable cwd — `--pin` keeps it past
the orphan sweep.

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
> landed.** Confirm via `waypoint sessions events <sid> --compact` before
> resending — a duplicate message can cause a worker to execute a task twice.
