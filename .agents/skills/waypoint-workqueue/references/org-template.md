# The Crew (organization template)

Start from this organization instead of designing one per job, then make it your
own. It is a flat crew: one lead, some interchangeable workers, an optional
reviewer. The roles, counts, and backends below are a launch point, not a rule —
scale them to the job, and reshape the structure when the work calls for it.

## Roles

- **Lead** — you. Splits the job, hands out tasks, checks and merges results,
  owns every board cell. Exactly one lead.
- **Workers** — spawned `subagent:` sessions. Each does one task at a time, then
  reports. Interchangeable in role, but **not necessarily identical**: a worker
  can run any backend/model (`claude_code`, `codex`, `opencode`, `tmux`) and live
  in its own worktree or even a different repo. Mix them to fit the work — cheap
  models on mechanical tasks, a stronger one where it matters; place each worker
  where its task's code is (how to choose: `references/backends.md`). As many as
  the job needs.
- **Reviewer** (optional) — one extra session, ideally a different model, that
  the lead asks to sanity-check a result before merging. Add it only for
  high-stakes jobs; skip it otherwise.

## The shared channel

One channel for the whole job: `job:<job-id>` (a short slug, no slashes — e.g.
`job:drop-py38`). Everything lives here, so `/board` shows the job's progress at
a glance.

Two kinds of cell, **both written only by the lead**:

- `plan` — the job in one cell: goal, integration branch, and the task list.
- `task:<n>` — one per task. The text is the instruction plus how to check it;
  `--meta` carries the live status.

```bash
# plan — once
waypoint board post job:drop-py38 \
  "Drop Python 3.8 support. Integration branch wq/drop-py38. 12 tasks, one per package." \
  --key plan

# a task — text is the contract, meta is the status
waypoint board post job:drop-py38 \
  "pkg/auth: remove the 3.8 shims and set requires-python >=3.9. Check: uv run pytest pkg/auth." \
  --key task:3 --meta state=todo
```

`state` is `todo | doing | done | blocked`. Workers report progress to the **log**
(a plain `waypoint board post job:<id> "..."`) or by a direct send; the lead
updates the `task:<n>` cell. Workers never write cells — a worker's own posts
vanish when it is reaped, so durable state stays with the long-lived lead.

> **Pitfall — `--key` is an upsert that REPLACES the cell's text.** There is no
> text-preserving metadata patch (`board edit-entry` also requires the text). So
> to flip a task's status, **re-post the cell with its full contract text** plus
> the new `--meta`; keep that text in a shell variable so you can repost it
> verbatim. Never post a keyed cell with throwaway text (`"task 3 -> <sid>"`) just
> to change `state` — that silently destroys the contract the worker reads, and
> the worker will (correctly) report blocked for want of scope. Track the
> assignee in `--meta`, not in the text.

## Handing a task to a worker

Spawn the worker in the task's worktree (see `references/playbook.md`), then send
this fixed message — fill in the two ids and the number:

```bash
waypoint sessions send <worker-sid> \
  "[wp-msg from=<lead-sid>] You are a work-queue worker. Read job:<job-id> task:<n>, do exactly that task in your cwd, verify it with the task's check, then post 'task <n> done — branch <branch>' to job:<job-id> and go idle. Touch nothing else."
```

## Lifecycle (five steps)

1. Lead writes `plan` and one `task:<n>` per task.
2. Lead gives each free worker a task — a worktree plus the message above — and
   sets that task `doing`.
3. Worker does it, self-checks, reports `done` (or `blocked`).
4. Lead checks the result against the task, merges the worker's branch, sets the
   task `done` (or hands it back as `todo`).
5. Repeat until every task is `done` or `blocked`; then run the full check on the
   integration branch and report.

## Variant: add a reviewer

For risky changes, before the step-4 merge the lead asks the reviewer session (a
different model) "does branch `wq/<job>-t<n>` do exactly `task:<n>`?" and
merges only on a yes. One extra session; nothing else changes.
