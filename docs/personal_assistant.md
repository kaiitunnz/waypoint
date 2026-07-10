# Personal assistant

The personal assistant is a single, long-lived conversation thread, separate
from the coding sessions you launch for tasks. Like any session it is an
(agent, transport) pair — a coding agent driven over a chosen interface — but
it is created and kept alive by the runtime, reachable from a dedicated page in
the app. Use it to ask about the host machine, to ground questions in your
running Waypoint sessions, and to spin up or steer those sessions on your
behalf.

It is not a new backend — it reuses the existing plugin, runtime, storage, and
streaming machinery. Its only distinguishing traits are a dedicated session
`source`, deletion/termination protection, and the out-of-band tooling
described below.

## Enabling it

Add an `assistant` block to `waypoint.yaml` (see `backend/waypoint.example.yaml`):

```yaml
assistant:
  enabled: true                       # omit the whole block to disable
  backend: claude_code                # defaults to `default_backend`
  account_profile_id: work            # named account/config profile for first creation
  transport: claude_cli               # the agent's default interface if unset
  model: opus                         # must exist in the backend's catalogue
  effort: high                        # ignored by backends without an effort knob
  permission_mode: bypassPermissions  # see the security note below
```

`backend`, `account_profile_id`, `transport`, `model`, `effort`, and
`permission_mode` seed the thread; they can be changed live from the assistant
page, so treat them as defaults rather than a lockdown. The profile selects the
backend's named config/account root and is validated like any other session
launch. `transport` must be one of the agent's supported interfaces; omitting it
uses the agent's default (the Emulated chat interface for Claude Code). The
block is enabled by default when present — set `enabled: false` to keep the
config but turn the assistant off.

The assistant always runs in a managed working directory (`<data_dir>/assistant`),
so it has no `cwd` setting. The runtime links repo-tracked assistant assets into
that directory: `AGENTS.md`, `CLAUDE.md`, `.agents/skills`, `.claude/skills`,
and `.codex/skills`. The bootstrap files stay small and point the agent at the
local `waypoint` and `waypointctl` skills, which hold the detailed operational
workflows. There is no visible bootstrap message in the transcript. The working
directory is only a scratch cwd; shell access reaches the whole host, so host
inspection is unaffected.

## Lifecycle

- On startup the runtime refreshes the assistant workspace asset links, then
  reuses any still-alive assistant thread that lives in the managed workspace,
  **regardless of its agent or interface**. The live thread is the source of
  truth, so an agent and interface chosen from the UI survive a redeploy. The
  `assistant` block in `waypoint.yaml` only seeds the *first* creation; editing
  `assistant.backend` / `assistant.transport` later has no effect while a thread
  exists — clear the context to re-seed.
- If no live thread exists (first boot, or the previous one exited), a fresh
  thread is created from the `waypoint.yaml` defaults.
- The assistant cannot be **deleted**, and the generic session terminate/delete
  endpoints reject it (it is a protected singleton).

### Controls (assistant page)

The **Session settings** editor (opened from the composer overflow `+` menu)
is the full editor for the assistant. It stages every safe change and applies
them in one operation: title, tuning (model / effort / permission mode), account
profile, and — for restart-capable agents — custom CLI args, config overrides,
and launch-environment keys (add / replace / remove, values never shown).
In-place changes restart-and-resume the same singleton; changing the agent,
interface, or resuming a thread stages a replacement (the reset/attach
lifecycle) and disables the advanced launch fields, since the replacement
contract does not carry them. A single warning states the real restart /
interrupt outcome before Apply.

The **settings popover** (⚙, next to the composer) remains as a quick-tuning
shortcut. Changing
the agent, the interface, or picking a thread there stages an inline confirm,
since each replaces the conversation:

- **Agent / Interface** — rebuild the assistant on a different coding agent or
  interface. When the target agent hosts profiles, choose its **New conversation
  account** for the replacement. The
  conversation cannot migrate between agents, so this starts a fresh thread at
  the new agent's default interface, model, effort, and permission mode.
- **Resume thread** — attach the assistant to an existing backend-native thread
  (one discovered by the chosen agent and New conversation account). The thread
  is imported as-is over the agent's native interface: it resumes its own
  conversation and working directory, so the assistant charter — which lives in
  the managed workspace — does **not** apply, and the **Interface** picker does
  not. Offered only for agents that support thread discovery and import.
- **Account** — restart and resume the current assistant conversation under a
  different named profile. This is the normal running-session account switch;
  it keeps the same conversation rather than creating a replacement.
- **Model / effort / permission mode** — applied live to the running thread; no
  context is lost.

The **overflow menu** (⋯) holds the standalone lifecycle actions, mirroring
where ordinary sessions keep terminate/delete:

- **Clear context** — start a fresh thread on the same agent and interface,
  keeping the live model/effort/permission mode and account profile.
- **Terminate / Reattach** — stop the thread (keeping it the pinned singleton)
  and later revive the same conversation. Reattach is offered only when the
  backend can resume after exit.

Switching agent or interface, attaching a thread, and clearing context all
replace the current conversation: the previous thread is **demoted to an
ordinary stopped session** (its transcript is preserved and it becomes
deletable), never destroyed.

A terminated assistant survives reattach only within the running deployment; a
redeploy cannot reattach an exited thread, so it demotes that thread to a normal
stopped session and creates a fresh assistant. Likewise, an attached thread
lives outside the managed workspace, so a redeploy will not re-adopt it — it is
demoted and a fresh assistant is created from the `waypoint.yaml` defaults.

Both the Waypoint session id and the backend-native thread id (e.g. the value
for `claude --resume`) are surfaced on the assistant page and via `/api/me`, so
the thread can be recovered outside the app if needed.

Asset updates do **not** recreate or demote the live assistant. Backends are
expected to pick up updated `AGENTS.md`, `CLAUDE.md`, and skill files from the
workspace themselves.

## Managing sessions: the `waypoint sessions` CLI

The `waypoint` skill is the authoritative assistant workflow for this CLI.
At a high level, the assistant manages your coding sessions through the
`waypoint` CLI, which
talks to the running server over HTTP (distinct from the in-process `waypoint
session` commands):

```
waypoint sessions list
waypoint sessions show <id>
waypoint sessions events <id> [--messages N] [--before-sequence S]
waypoint sessions start --backend <id> --cwd <path> [--model M] [--effort E]
waypoint sessions send <id> <text>
waypoint sessions interrupt|terminate <id>
waypoint sessions approve <id> <decision> [--text T] [--approval-id A]
```

Output is JSON. The same `WaypointClient` (in `waypoint.client`) backs the CLI
and can be imported directly.

### Authentication

`waypoint serve` writes a token to `<data_dir>/cli-token` (mode `0600`) on
startup. The CLI resolves auth in order: the `WAYPOINT_TOKEN` env var, that
token file, then a password login (`WAYPOINT_PASSWORD` or the configured
password), caching the result back to the file. On the same host the assistant
needs no secret — it reads the token file.

## Security

The assistant is high-privilege: it has shell access to the host (to answer
environment questions) and can create, message, and terminate other agent
sessions. Run it in an autonomous `permission_mode` so it isn't blocked on
per-action approvals — but understand the blast radius before exposing the
service beyond localhost. The `cli-token` file grants full API access,
equivalent to the password; treat it as a secret.
