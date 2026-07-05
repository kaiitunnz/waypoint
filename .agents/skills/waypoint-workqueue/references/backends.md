# Choosing harnesses and models

The lead routes each task to a **backend** (which harness/agent runs it) and a
**model**. Backend choice is about capability and environment; model choice is
about task difficulty and cost. Both are set per worker at spawn time
(`--backend` / `--model`, see `references/playbook.md`).

## Discover what is actually available first

Do not assume a backend or model exists — the install varies per deployment.

```bash
waypoint doctor      # which backend CLIs are installed on this host
waypoint backends    # registered plugins + their capability descriptors
waypoint models      # the model ids and reasoning efforts each backend offers
```

`codex` and `opencode` discover their model lists live; `claude_code` serves a
static curated Claude catalogue, which it reuses over the Emulated `claude_tty`
transport as well (`waypoint models claude_tty` returns the same ids). `waypoint
models` reports the exact ids and efforts
each backend offers right now — run it and pass those ids verbatim to `--model`
/ `--effort`, rather than guessing from memory. With no argument it sweeps every
selectable backend id (`tmux`, a pure transport, is excluded); a backend
whose live discovery is down is reported with an `error` entry instead of
failing the sweep. Pass one (`waypoint models codex`) to query a single backend. The names below are
illustrative (mid-2026) and will age — trust `waypoint models` over them.

Watch for **context-window variants**: an agent may list the same model under
distinct ids that differ only in context size (e.g. claude_code's `sonnet` vs
the 1M-context `sonnet[1m]`). Workers do real implementation in their own tree,
so route them to the large-context variant — the small-window id thrashes and
auto-compacts on a non-trivial task.

If you are unsure whether the harness or model you want is available, or whether
it is the right fit for the job, **ask the user** (use the ask-question tool if
your harness has one) rather than guessing. The user is the fallback — never pick
a backend or model blindly.

A **preset** can pin a whole worker profile — backend, model, transport, effort,
and permission mode — so a crew's workers spawn from one named default instead of
re-deriving each field per task (`waypoint presets list`, then `sessions start
--preset <id>`; see `waypoint-subagents`'s `references/spawn-and-poll.md`). A
preset is a convenience layer, not a substitute for the discovery above: still
run `waypoint backends` / `waypoint models` when creating or overriding one so the
ids it pins are real, and confirm its permission posture before trusting it for
unattended workers.

## The harnesses

- **`claude_code`** — wraps the Claude Code CLI (Claude models). Structured;
  inline model and permission-mode swaps (including a `plan` mode), thread
  fork/import, and tool approvals that can carry notes. Strongest at deep
  reasoning, refactoring, and ambiguous or wide-blast-radius work. *Reach for it
  for the hard, high-stakes tasks.*
- **`codex`** — wraps the OpenAI Codex app server (GPT models, live model list).
  Structured; inline model **and** effort (per turn, no restart), plan approval,
  `/compact`, session-scoped "accept for session" approvals. Fast and strong at
  autonomous, well-specified execution; token-efficient. *Reach for it for
  high-volume, clearly-specified, or cost-sensitive tasks.*
- **`opencode`** — wraps OpenCode (model-agnostic; many providers, including
  local/self-hosted; live discovery). Structured; inline model and effort,
  agents/rulesets as permission modes, `/compact`. *Reach for it when you need a
  specific, open, or local model, want to spread load across providers, or must
  avoid one vendor's rate limits.*
- **`claude_tty`** — not a fourth agent: it is the `claude_code` agent driven
  over the **Emulated** TTY-tail transport. It launches Claude Code's interactive
  TUI in a tmux pane and tails its transcript JSONL into the canonical event
  stream, so the transcript **is** structured (`is_structured=True`) — same
  Claude model catalogue, accepts `--model`. Tool approvals go through pane
  keystrokes rather than the stdio hook, and the TUI is exempt from the
  `claude -p` API rate limit. Select it with `--backend claude_code --transport
  claude_tty` (and `claude_code` now **defaults** to this transport); `--backend
  claude_tty` stays as a legacy alias. *Reach for the Emulated transport for
  autonomous Claude sessions or as a fallback when the structured `claude_cli`
  adapter is unavailable.*
- **`tmux`** — a raw transport, not a selectable agent backend: no model /
  effort / permission knobs, unstructured (scraped) transcript, but full shell
  and host access. It backs `claude_tty` and other TTY-style sessions
  internally; you do not spawn workers with `--backend tmux` directly.

Mixing is the point: a crew can run different harnesses side by side, each placed
where its task's code lives (`references/org-template.md`).

## Model tiers (coarse, by difficulty × cost)

Route by how hard and how risky the task is, not by chasing a leaderboard:

- **Frontier / deep-reasoning** — ambiguous, architectural, or wide-blast-radius
  tasks; the ones a wrong answer is expensive on. (e.g. Claude Opus / Fable,
  GPT-5-class.)
- **Balanced / daily-driver** — the default for most well-specified tasks. (e.g.
  Claude Sonnet, mid GPT-5.)
- **Fast / cheap** — mechanical, repetitive, low-ambiguity tasks at volume; the
  bulk of a migration or codemod. (e.g. Claude Haiku, a mini model, or an open
  model via `opencode`.)

Heuristics:

- Put the cheap tier on the long tail of mechanical items and reserve the
  frontier tier for the few genuinely hard ones — that split is most of the cost
  win of a heterogeneous crew.
- When unsure of a model's fit for a task class, send **one pilot task**, check
  the result, then fan out at that tier.
- A task that fails review twice is a signal to bump it up a tier, not to retry
  at the same one.

These tiers are stable; the specific models filling them are not. Re-check with
`waypoint doctor` / `waypoint backends` and your current model knowledge each
time.
