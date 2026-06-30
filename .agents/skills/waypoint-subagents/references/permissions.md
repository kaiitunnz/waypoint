# Permissions and Approvals

A child you spawn inherits your permission posture by default (see below). If
that posture is `default`, the child pauses for approval before running tools —
and an agent cannot answer its own child's prompts unless you service them. So
decide how the child should handle approvals up front.

## Discover what a backend supports

Do not hard-code mode names — they differ per backend and change over time. Ask:

```bash
waypoint backends
```

For each backend this lists `permission_modes` (the ids you can pass to
`--permission-mode`), `approval_decisions` (the verbs you can pass to `sessions
approve`), and `supports_plan_approval`. Read it for the child's backend before
spawning.

## The child inherits your posture automatically

```bash
waypoint sessions start --backend <id> --cwd <path> --title "subagent:<purpose>"
```

When you spawn from inside a session, the server **automatically gives the child
your own permission mode** — the CLI passes your session id (`WAYPOINT_SESSION_ID`)
and the child inherits your mode when it shares your backend (cross-backend
spawns fall back to `default`, since modes are not portable). So you usually do
**not** pass `--permission-mode` at all: by default the child is no more
permissive than you, which is the safe baseline.

## Choosing a different mode

To override the inherited mode, pass it explicitly:

```bash
waypoint sessions start --backend <id> --cwd <path> \
  --title "subagent:<purpose>" --permission-mode <mode>
```

An explicit `--permission-mode` always wins, and it **may widen** beyond your own
posture — but widening is a deliberate act, not a default. Guidance:

- **Narrowing** (e.g. you are on `auto` but want the child in `default` to
  service its approvals yourself) — fine, do it whenever the child's work is not
  fully trusted.
- **Widening** (granting the child a more permissive mode than you have) — only
  with justification. If you are a **top-level session** (you have no spawner of
  your own — check whether your own record has a `spawner_session_id`), **ask the
  user** before widening. If you are yourself a subagent, do not widen on your
  own; inherit.
- Pick the **least** permissive mode that lets the child proceed. Discover the
  ids and which auto-approve via `waypoint backends` (claude_code's
  auto-approving modes are `auto` / `dontAsk` / `bypassPermissions`; codex
  `full_access`; opencode `allow`). An auto-approving child runs tools without
  prompting, so only choose one for work you would pre-approve yourself.
- **When the child must run unattended, decide its posture before spawning.** A
  child left on `default` parks silently on its first approval — it sits in
  `waiting_input` while you block waiting for it, with no prompt back to you. If
  you cannot determine a safe auto-approving mode yourself, **ask the user** which
  `--permission-mode` to use (use the ask-question tool) rather than spawning on
  `default` and discovering the stall later. Leaving it on `default` is only the
  right default when you will be **interactively servicing** the child's approvals
  yourself.

## Fix a posture without respawning

If a worker is already stalling on `default`, you do **not** have to reap and
respawn it. On structured agents (claude_code / codex / opencode), including
over the Emulated (`claude_tty`) transport, you can widen its mode in place:

```bash
waypoint sessions set-permission-mode <child-id> <mode>   # alias: sessions mode
```

The command validates `<mode>` against the backend's advertised ids and reports
them on a mismatch. Backends that can't change mode live are rejected cleanly —
for those, reap and respawn remains the only path.

## Service a child's approvals

When a child sits in `waiting_input`, it may be blocked on an approval. Read it
from the child's coalesced events, then decide:

```bash
waypoint sessions events <child-id> --messages 20 --coalesce   # find the approval_request
waypoint sessions approve <child-id> <decision> [--approval-id <id>]
```

Coalescing preserves `approval_request` records and keeps surrounding
assistant/tool output readable. Use raw `events` only if you need exact event
ordering, duplicate diagnosis, or per-chunk metadata.

The `approval_request` event metadata tells you the kind:

- **Tool-use approval** (`method: can_use_tool`) — carries `tool_name`,
  `tool_input`, and `approval_id`. Decide per the tool and its inputs.
- **Plan approval** (backends with `supports_plan_approval`, e.g. codex) —
  carries a plan to accept or reject; decisions include `acceptForSession` to
  stop re-prompting for the rest of the session.

Valid `<decision>` values are backend-specific — take them from
`approval_decisions` in `waypoint backends` (e.g. `approve`/`decline`, plus
`acceptForSession` on codex/opencode). Pass `--approval-id` when several are
pending.

Do not approve destructive, privileged, or unclear requests on a child's behalf
— surface them to the user, exactly as for your own session.

## Service a child's questions

A child can also block on a **question** (an `AskUserQuestion` prompt), which is
distinct from an approval and is serviced through a different command. It
surfaces in the child's events as a `tool_call` whose metadata has
`tool_name: AskUserQuestion` (carrying the question text and options under
`payload.input.questions`); on some backends the child also reports
`waiting_input`. There is no `approval_request` for it, so `sessions approve`
will not release it.

```bash
waypoint sessions events <child-id> --messages 20 --coalesce   # find the AskUserQuestion tool_call
waypoint sessions answer-question <child-id> --answer "<your answer>"
```

- Answer in plain text with `--answer`. For the structured multi-question shape,
  pass `--answers-json '[{"question": "...", "answer": "...", "notes": "..."}]'`.
- Pass `--tool-use-id <id>` to target a specific question when several are
  pending; omit it to answer the sole pending one.
- **Do not** use `sessions send` to answer a question — injecting a message does
  not satisfy the blocking prompt, so the child stays parked. `answer-question`
  is the only command that releases it.
- As with approvals, do not answer on the child's behalf when the choice is the
  user's to make — surface it instead.
