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
static curated list; `claude_tty` has none (it tails Claude's TTY output, no
separate model registry). `waypoint models` reports the exact ids and efforts
each backend offers right now — run it and pass those ids verbatim to `--model`
/ `--effort`, rather than guessing from memory. With no argument it sweeps every
selectable agent backend (`tmux` is a transport and is excluded); a backend
whose live discovery is down is reported with an `error` entry instead of
failing the sweep. Pass one (`waypoint models codex`) to query a single backend. The names below are
illustrative (mid-2026) and will age — trust `waypoint models` over them.

If you are unsure whether the harness or model you want is available, or whether
it is the right fit for the job, **ask the user** (use the ask-question tool if
your harness has one) rather than guessing. The user is the fallback — never pick
a backend or model blindly.

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
- **`claude_tty`** — a TTY-tail wrapper around `claude_code`: launches Claude
  Code in a tmux pane and scrapes its terminal output. Unstructured transcript
  (no hook events), but selectable as `--backend claude_tty` and accepts
  `--model`. *Use as a fallback when the structured `claude_code` transport is
  unavailable — not as a first choice.*
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
