# Spawn and Poll

## Spawn a child

```bash
waypoint sessions start \
  --backend <agent-id> \
  --cwd <path> \
  --title "subagent:<short-purpose>" \
  [--transport <transport-id>] [--model <model>] [--effort <effort>] [--permission-mode <mode>]
```

- Pick `--backend` (the agent) deliberately — this is the main reason to use a
  Waypoint sub-session over a harness subagent. Use `waypoint backends` to see
  which agent ids are registered (and their `supported_transports`), and
  `waypoint doctor` to check local CLI availability when launch fails.
- `--transport` is an optional secondary axis selecting the interface — Chat
  (`claude_cli`), Emulated (`claude_tty`), or Terminal (`tmux`). Omit it to take
  the agent's `default_transport` (a local `claude_code` child defaults to the
  Emulated `claude_tty` transport). Set it explicitly when the task needs a
  specific interface; `claude_tty`/`tmux` are transports, not `--backend` values.
- Pick `--model` / `--effort` from `waypoint models <backend>`, which reports the
  ids and efforts that backend actually offers. Pass them verbatim; do not guess
  model names from memory. Some agents list several **context-window variants**
  of the same model under distinct ids (e.g. claude_code exposes `sonnet` and a
  separate large-context `sonnet[1m]`); for a child doing real implementation
  work, prefer the large-context variant — a small-window model thrashes and
  auto-compacts mid-task on anything non-trivial.
- Pick `--cwd` deliberately. For repo work, pass the repo root; for scratch or
  host inspection, pass an explicit safe directory. Do not assume the child
  should inherit your own working directory.
- Always set the `subagent:` title prefix so the session is recognizable as one
  you own.
- A child on the **same backend** inherits your permission mode automatically;
  cross-backend children fall back to that backend's default. Pass
  `--permission-mode` only to override this. See `references/permissions.md`.
- Capture the returned session id. Keep it for the rest of the turn.

Then send the task as the first input:

```bash
waypoint sessions send <session-id> "<task instructions>"
```

## Do not assume the child can load your skills

A child does **not** automatically inherit the skills you are running. Each agent
discovers skills from its **own** skill directories, and those roots differ per
agent and shift over time (e.g. Claude Code reads `~/.claude/skills` and a repo
`.claude/skills`; Codex reads `.codex/skills`; OpenCode has its own) — confirm the
roots for the child's backend rather than trusting these examples. A repo's
`.agents/skills` only reaches an agent if installed/symlinked into its roots
(`scripts/install_skills.sh`, or `waypointctl skills install`). Discovery is
silent when it misses — the agent just behaves as if the skill does not exist.

So when the child's task depends on a skill — and **especially across backends**,
where you cannot assume the same skills are installed — do not name the skill and
trust it loaded. Instead:

- Put the essential steps directly in the `sessions send` message, treating any
  skill as an optional enhancement rather than a prerequisite, and/or
- confirm the skill is installed for that backend before relying on it (its
  `SKILL.md` present under that agent's skills dir), and
- verify from the child's events that it actually did the work, not that it
  merely acknowledged a skill name.

## Wait to completion

`waypoint sessions wait` blocks until the child reaches a terminal/idle status,
prints nothing until then, and emits the final `{"session": {...}}` once. Use it
instead of a hand-rolled poll loop:

```bash
sid=<session-id>
waypoint sessions wait "$sid" --timeout 600   # cap the wait; tune per task
```

By default it stops on these statuses:

- `idle` / `waiting_input` — the child has finished its turn (possibly awaiting
  more input, an approval, or an answer to a question — see
  `references/permissions.md`).
- `exited` — the child process ended.
- `error` — the child failed; read its events to find out why.

Statuses that mean keep waiting: `starting`, `running`, `interrupted`.

It maps the outcome to a process exit code so it composes in `&&` chains:
`error` → 1, timeout → 124, everything else → 0. Narrow the set with `--until`
(comma-separated), e.g. wait only for the process to end, then dump events:

```bash
waypoint sessions wait "$sid" --until exited,error --timeout 600 \
  && waypoint sessions events "$sid"
```

To watch a child's transcript live instead of blocking silently, stream events
as NDJSON (one compact JSON object per line) until a terminal status or Ctrl+C:

```bash
waypoint sessions events "$sid" --follow
```

The CLI always prints JSON; `sessions show <id>` emits `{"session": {...}}`, so
when you do need a one-off status read it lives at `.session.status`. The status
values are lowercase: `starting`, `idle`, `waiting_input`, `running`,
`interrupted`, `exited`, `error`.

## Fan-out

To run several children in parallel, start them all, collect their ids, then
poll each (or loop over the set each tick). Keep the count small and
intentional — there is no server-side cap, so an unbounded loop will exhaust the
host.
