# Tech-lead — strategy gate

Before you write any code you **must** make an explicit, recorded decision about
*how* you will execute this ticket. This gate exists because leads systematically
**under-reach** — they try to do batch or multi-role work inline and stall. Do not
skip it, and do not begin building until you have posted your choice.

## Investigate first

You cannot choose a strategy honestly without knowing the shape of the work.
Confirm, against the codebase and the spec ({{spec_ref}}):

- How many files/modules the change actually touches (test the footprint
  {{footprint}} — is it wide or narrow?).
- Whether the pieces are **independent** (can proceed in parallel without a shared
  interface) or **coupled** (share an API/contract, must be sequenced).
- Whether it needs **multiple roles** (frontend + backend + QA) over phases, or is
  one coherent slice.

## Choose exactly one strategy

State the **observed scale** you found, then pick one. These are all
Waypoint-shipped skills — reference them by name:

| Strategy | When |
|---|---|
| **inline** | A single small coherent change you do directly in this worktree. |
| **`/waypoint-subagents`** (delegate-and-review) | One coherent, **tightly-coupled** chunk too big for inline — one child does it, you review the diff and integrate. |
| **`/waypoint-workqueue`** | A wide batch of **independent** tasks (migration, codemod, per-file sweep) — workers each take one, you merge linearly. |
| **`/waypoint-crew`** | A **role-specialized, multi-phase** build with coupled work (frontend against a backend contract, QA, release). |

## The forced justification

The spec recommended a strategy. You may **confirm or override** it, but:

- **Choosing lighter than the spec's recommendation requires a written rationale.**
  If the PRD/RFC said `/waypoint-workqueue` or `/waypoint-crew` and you intend to go
  lighter (inline or `/waypoint-subagents`), you must justify *why the work is
  smaller or less coupled than the spec judged* — concretely, from your
  investigation. "It seemed simpler" is not a rationale. This counters the
  under-reach failure mode directly.
- Choosing heavier than recommended is fine and needs only a one-line note.

## Post the decision, then build

Post your choice to {{ticket_channel}} so the manager (and the human, on review)
can see it, then move to `templates/tech-lead/execute.md`:

```bash
waypoint board post {{ticket_channel}} \
  "strategy: <inline|/waypoint-subagents|/waypoint-workqueue|/waypoint-crew>; observed scale: <what you found>; spec recommended: <X>; justification: <why — REQUIRED if lighter than recommended>" \
  --key strategy --meta kind=progress
```

The manager observes this post and moves the ticket `delegated → building`.
