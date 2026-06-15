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
  can run any backend/model (`claude_code`, `codex`, `opencode`) and live
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

Three kinds of cell, **all written only by the lead**:

- `plan` — the job in one cell: goal, integration branch, and the task list.
- `task:<n>` — one per task. **Immutable contract**: the text is the instruction
  plus how to check it. Set once; never rewritten.
- `status:<n>` — paired with each `task:<n>`. **Mutable status**: `--meta`
  carries `state=todo|doing|done|blocked` and `assignee=<sid>`. Text is a brief
  label. Update with `waypoint board set-meta` to flip state without touching text.

```bash
# plan — once
waypoint board post job:drop-py38 \
  "Drop Python 3.8 support. Integration branch wq/drop-py38. 12 tasks, one per package." \
  --key plan

# task cell — immutable contract, written once
waypoint board post job:drop-py38 \
  "pkg/auth: remove the 3.8 shims and set requires-python >=3.9. Check: uv run pytest pkg/auth." \
  --key task:3

# status cell — created alongside the task, updated as work progresses
waypoint board post job:drop-py38 "task 3 status" --key status:3 --meta state=todo

# flip state without resupplying text
waypoint board set-meta job:drop-py38 --key status:3 --meta state=doing --meta assignee=<sid>
```

`state` is `todo | doing | done | blocked`. Workers report progress to the **log**
(a plain `waypoint board post job:<id> "..."`) or by a direct send; the lead
updates `status:<n>` cells. Workers never write cells — a worker-authored keyed
cell is pruned when the worker is reaped. Keyless log posts are durable history:
they survive reap and persist as long as the channel does. Read the job's history
with `board log job:<id>`; confirm what landed from git (ground truth for code),
the log is the narrative. Durable state (cells) stays with the long-lived lead.

> **Why two cells? — `--key` is an upsert that REPLACES the cell's text.** There
> is no text-preserving metadata patch (`board edit-entry` also requires the text).
> With a single cell, flipping state forces you to re-post the full contract text or
> silently clobber it — posting a stub like `"task 3 -> <sid>"` destroys the scope
> the worker reads, and the worker will (correctly) report blocked. The two-cell
> shape avoids this by design: `task:<n>` is the immutable contract (never touched
> after creation), and `status:<n>` is the mutable status cell updated with
> `set-meta`. Track the assignee in `status:<n>`'s `--meta`, not in text.

## Handing a task to a worker

Spawn the worker in the task's worktree (see `references/playbook.md`), then send
this fixed message — fill in the two ids and the number:

```bash
waypoint sessions send <worker-sid> \
  "[wp-msg from=<lead-sid>] You are a work-queue worker. Read job:<job-id> task:<n>, do exactly that task in your cwd, verify it with the task's check, then post 'task <n> done — branch <branch>' to job:<job-id> and go idle. Touch nothing else."
```

## Lifecycle (five steps)

1. Lead writes `plan`, one `task:<n>` per task, and one `status:<n>` per task
   (state=todo).
2. Lead gives each free worker a task — a worktree plus the message above — and
   sets `status:<n>` to state=doing with assignee=\<sid\>.
3. Worker does it, self-checks, reports `done` (or `blocked`).
4. Lead checks the result against the task, merges the worker's branch, sets
   `status:<n>` to done (or todo to hand it back).
5. Repeat until every task is `done` or `blocked`; then run the full check on the
   integration branch and report.

## Variant: add a reviewer

For risky changes, before the step-4 merge the lead asks the reviewer session (a
different model) "does branch `wq/<job>-t<n>` do exactly `task:<n>`?" and
merges only on a yes. One extra session; nothing else changes.
