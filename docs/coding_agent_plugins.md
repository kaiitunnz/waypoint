# Coding-agent backend plugins

Waypoint orchestrates each coding session as an **(agent, transport) pair** through a plugin registry. The *agent* (`claude_code`, `codex`, `opencode`) owns the protocol, model/thread discovery, the event normalizer, and the `AgentLaunchContract` — the transport-agnostic launch knowledge (`launch_flags`, `pregenerate_thread_id`, `resume_args`, `conversation_exists`, `capture_thread_id`). The *transport* (a native structured adapter per agent, the generic `tmux` pane wrapper, or the `claude_tty` tty-tail) owns `send`, `interrupt`, `approval`, and lifecycle, and declares the channel flags `is_structured` / `supports_resume` / the inline-set knobs.

The split matters because the generic transports drive *any* agent without knowing which one it is: the launch knowledge lives on the agent, so the `tmux` pane wrapper holds **no** `if backend == "codex"` branches — it calls `registry.get(session.backend)` and dispatches through the `AgentLaunchContract`. A new agent gets pane-wrapping for free.

The runtime, API, scheduler, storage, and frontend catalog all dispatch by plugin id; adding a new backend means writing a plugin module, not editing the core. This doc covers the design, the two contracts a plugin satisfies, the capability split, and recipes for shipping a new agent or transport.

## Goals

- One place per backend for everything backend-specific: lifecycle,
  control surface, model/thread discovery, transport, event
  normalization, route registration.
- The runtime — and the generic transports — stay agent-agnostic. No
  `if backend == "codex"` branches anywhere in `runtime.py`, `api.py`,
  `scheduler.py`, or `backends/tmux/`.
- The frontend reads a live catalog (`/api/backends`) so picker
  dropdowns, badge palettes, permission-mode lists, and slash-command
  hints update without TypeScript edits.
- New protocol-version bumps for an existing agent stay inside that
  agent's module.

## Architecture at a glance

```
backend/src/waypoint/
├── runtime.py           ← generic dispatcher (no backend literals)
├── api.py               ← generic REST (/api/backends/{id}/...)
├── scheduler.py         ← generic; consults plugin permission_modes
├── storage.py           ← generic; sessions table is plugin-agnostic
├── transports/
│   └── base.py          ← TransportAdapter ABC
└── backends/
    ├── base.py          ← BackendPlugin Protocol + AgentLaunchContract
    │                       Protocol + DefaultLaunchContract mixin
    ├── capabilities.py  ← BackendCapabilities (flat compat aggregate)
    │                       + AgentCapabilities + TransportCapabilities
    ├── events.py        ← EventEnvelope (versioned metadata schema)
    ├── registry.py      ← BackendRegistry, get_registry()
    ├── bootstrap.py     ← build_default_registry() — registration entry
    ├── claude_code/
    │   ├── plugin.py         ← agent: launch contract + structured adapter
    │   ├── adapter.py        ← stream-json subprocess driver
    │   ├── transport.py      ← TransportAdapter impl
    │   ├── normalize.py      ← stream-json → EventEnvelope helpers
    │   ├── permission_modes.py
    │   ├── models.py         ← static model alias table
    │   ├── support.py        ← host-side support bundle (thread enumerator)
    │   ├── threads.py        ← local thread enumeration
    │   └── threads_remote.py ← SSH thread enumeration
    ├── claude_tty/          ← claude_code agent over a tty-tail transport
    │   ├── plugin.py         ← composes ClaudeCodePlugin + TmuxPlugin
    │   ├── transport.py      ← tmux-pane input + approval driver
    │   ├── tailer.py         ← Claude transcript tailer
    │   └── normalize.py      ← transcript JSONL → EventEnvelope helpers
    ├── codex/
    │   ├── plugin.py
    │   ├── adapter.py        ← App Server SDK driver
    │   ├── transport.py
    │   ├── normalize.py      ← notification → EventEnvelope helpers
    │   └── permission_modes.py
    ├── opencode/
    │   ├── plugin.py
    │   ├── adapter.py        ← REST + SSE driver
    │   ├── transport.py
    │   ├── normalize.py      ← SSE event → EventEnvelope helpers
    │   ├── client.py         ← HTTP wrapper
    │   └── remote.py         ← SSH launcher
    └── tmux/                 ← generic pane wrapper; drives any agent
        ├── plugin.py         ← no backend literals; dispatches the contract
        ├── adapter.py
        ├── transport.py
        └── normalize.py      ← terminal-text scrape
```

## The two axes

A session's behavior is the product of two independent questions:

- **Which agent?** What CLI/protocol is running — its model catalogue,
  permission-mode vocabulary, thread/fork story, how it pins model and
  effort at launch, how it resumes a thread, how its native conversation
  id is discovered. This is the agent's knowledge and lives on the agent
  plugin.
- **How is it driven?** A structured stream (`claude -p`, the Codex App
  Server, OpenCode's SSE), a transcript tail that is still structured
  (`claude_tty`), or a scraped terminal pane (`tmux`). This is the
  transport's knowledge: whether the channel is
  structured, whether a detached session can be resumed, which control
  knobs apply inline vs. require a restart.

Two plugins can pair the same agent with different transports. `claude_code`
and `claude_tty` are both the **Claude agent**; the first drives it over the
structured stream-json adapter, the second over a tty-tail of the fullscreen
TUI. `claude_tty` therefore *composes* rather than reimplements: it holds a
`ClaudeCodePlugin` instance for agent knowledge and a `TmuxPlugin` instance
for the shared pane-wrapper transport (see
[`claude_tty/plugin.py`](../backend/src/waypoint/backends/claude_tty/plugin.py)).

## The plugin contract

A backend plugin is any object that satisfies the
`waypoint.backends.base.BackendPlugin` Protocol. The full surface lives
in [`backend/src/waypoint/backends/base.py`](../backend/src/waypoint/backends/base.py);
this section walks through it.

### Identity + capabilities (class attributes)

```python
class MyPlugin:
    id: str                     # registry key + storage column value, e.g. "opencode"
    transport_id: str           # storage column value, e.g. "opencode_http"
    label: str                  # human-readable, surfaced in pickers
    capabilities: BackendCapabilities
```

`BackendCapabilities` (frozen model; see
[`capabilities.py`](../backend/src/waypoint/backends/capabilities.py)) is the
single flat descriptor a plugin declares and the API serialises. It is the
**compatibility aggregate** over the agent/transport split: its field set and
order are frozen so `GET /api/backends` stays byte-identical, and it projects
to the two axis models via `split()` / `agent_capabilities()` /
`transport_capabilities()` (and recomposes via `from_split()`). The fields
partition cleanly along the axes below.

#### Agent-axis fields (`AgentCapabilities`)

Properties of the CLI/protocol itself, the same whichever transport drives it.

| Field | Type | What the runtime/frontend does with it |
|-------|------|----------------------------------------|
| `model_source` | `ModelSource` | `STATIC` (Claude — config + alias table), `LIVE_RPC` (Codex — `model/list`), `NONE` (tmux fallback). |
| `permission_modes` | `tuple[PermissionModeSpec, ...]` | Drives the picker, scheduler validation, and the catalog payload. |
| `effort_levels` | `tuple[str, ...]` | Empty tuple means "discovered per-model from the live model list" (Codex). |
| `slash_commands` | `tuple[SlashCommandSpec, ...]` | Frontend slash-suggestion list. |
| `approval_decisions` | `tuple[str, ...]` | Buttons rendered on the structured approval card. |
| `supports_thread_discovery` | `bool` | Enables `GET /api/backends/{id}/threads`. |
| `supports_thread_import` | `bool` | Enables `POST /api/backends/{id}/sessions/import`. |
| `supports_fork` | `bool` | Enables creating a new session from an existing backend thread. |
| `supports_plan_approval` | `bool` | Enables the structured plan-approval flow. |
| `supports_approval_note` | `bool` | Allows the approval response form to send a reviewer note back on decline. |
| `supports_attachments` | `bool` | Enables uploaded attachments in the composer for transports that can deliver paths or native image/file payloads. |
| `supports_custom_cli_args` | `bool` | Allows launch requests and configured targets to pass extra raw CLI arguments to the binary. |
| `supports_config_overrides` | `bool` | Exposes a separate `config_overrides` input wrapped per-agent (Codex's `--config K=V`). |
| `supports_slash_compact` | `bool` | Frontend hint that `/compact` is meaningful for this agent. |
| `cli_binary` | `str \| None` | Default CLI for local launches and tmux fallback; `None` opts out. Override via `plugin_configs.<id>.local_bin` (local) or `ssh_targets[*].plugin_configs.<id>.remote_bin` (per SSH target). |
| `target_aliases` | `tuple[str, ...]` | Substrings used to infer this agent from a tmux pane name. |
| `badges` | `dict[str, str]` | UI palette: `{"glyph": "X", "color": "#34d399"}`. |

#### Transport-axis fields (`TransportCapabilities`)

Properties of the channel — how the agent is driven.

| Field | Type | What the runtime/frontend does with it |
|-------|------|----------------------------------------|
| `is_structured` | `bool` | `False` flips the frontend transcript to the heuristic terminal-scrape renderer; `True` enables structured tool-pair cards. |
| `supports_resume` | `bool` | Enables the Resume button on detached sessions (tmux). |
| `supports_reattach_after_exit` | `bool` | `restore_session` can bring an `EXITED`/`ERROR` record back to `STARTING`; gates the reattach endpoint. |
| `supports_terminate` | `bool` | Whether the channel can tear a session down; today every plugin returns `True`. |
| `supports_set_model_inline` | `bool` | Gates the model picker + the `/api/sessions/{id}/model` endpoint. |
| `supports_set_effort_inline` | `bool` | Gates the effort picker. `False` if effort changes need a restart (Claude); the runtime still routes through `apply_effort`. |
| `supports_set_effort_with_restart` | `bool` | Gates effort changes that restart-and-resume between turns instead of applying mid-stream. |
| `supports_set_permission_mode_inline` | `bool` | Gates the permission-mode picker + the `/api/sessions/{id}/mode` endpoint. |
| `settings_change_interrupts_turn` | `bool` | Frontend hint that applying a model/permission/effort change relaunches the session and interrupts the running turn (claude_tty), so the composer confirms first. |
| `live_terminal` | `bool` | The transport drives the agent in a live terminal pane (a pty): the runtime tails its raw log to scrape state (`_ensure_monitor`) and the frontend can mirror the pane over the terminal websocket. True for the generic `tmux` wrapper only; structured transports (including `claude_tty`, which runs in a pane but is tailed from its transcript) publish to the event stream instead. |
| `is_fallback_for_managed_launch` | `bool` | Marks the transport the runtime falls back to when a structured agent's adapter isn't ready (or it isn't structured). Exactly one registered plugin sets this — today only `tmux`. |

> Migrating plugins to declare the two halves directly (and a per-axis
> registry) is a later phase. Today plugins declare one flat
> `BackendCapabilities`; the axis models exist as the typed split behind it.

### Built-in capability profiles

The built-in plugins expose the same contract but use different
mechanisms behind it:

| Backend | Agent | Transport | Models | Effort | Permission mode | Threads | Approval notes |
|---------|-------|-----------|--------|--------|-----------------|---------|----------------|
| `claude_code` | Claude | stream-json subprocess | Static catalogue; swaps via CLI control protocol | `low`/`medium`/`high`/`xhigh`; restart-with-resume while idle | Claude catalogue; swaps via control protocol | Discovers/imports Claude transcripts | Yes, on decline |
| `claude_tty` | Claude | fullscreen TUI in tmux, tailed from transcript JSONL | Static catalogue; restart-with-resume, interrupting any running turn | `low`/`medium`/`high`/`xhigh`; restart-with-resume, interrupting any running turn | Claude catalogue; restart-with-resume, interrupting any running turn | Discovers/imports Claude transcripts | No |
| `codex` | Codex | App Server SDK | Live `model/list` RPC | Per-turn flag from live model metadata | Codex catalogue; applied inline | Discovers/imports Codex threads | No |
| `opencode` | OpenCode | REST + SSE | Live OpenCode metadata | Backend-native setting | OpenCode catalogue; applied inline | Discovers/imports OpenCode sessions | Yes, on decline |
| `tmux` | (wraps any) | generic tmux pane | None | None | None | None | No |

### The agent launch contract

The generic `tmux` pane wrapper drives any agent without knowing which one it
is; the `claude_tty` tty-tail is bound to the Claude agent but reuses the same
machinery. Either way the agent-specific bits of that flow live on the agent as
the `AgentLaunchContract` Protocol (also in
[`base.py`](../backend/src/waypoint/backends/base.py)):

```python
def launch_flags(self, *, model, effort, permission_mode) -> list[str]: ...
def pregenerate_thread_id(self) -> str | None: ...
def resume_args(self, thread_id: str, prior_args: list[str]) -> list[str]: ...
async def conversation_exists(self, thread_id, cwd, launch_target) -> bool: ...
async def capture_thread_id(self, runtime, session_id, cwd, since, launch_target) -> None: ...
```

- `launch_flags` returns the CLI flags that pin model/effort/permission at
  process start, mirroring the structured-launch flag set. Omit flags the CLI
  doesn't accept (Codex has no `--effort`).
- `pregenerate_thread_id` returns an id to pass at launch, or `None`. Claude
  accepts `--session-id <uuid>` so the id is known up front; Codex only
  reveals its id after the first persist, so it returns `None`.
- `resume_args` translates launch args into the CLI's resume form (Claude
  prepends `--resume <id>` and scrubs any prior `--session-id`/`--resume`;
  Codex prepends the `resume <id>` subcommand).
- `conversation_exists` reports whether the agent has persisted the thread to
  disk yet — both Claude and Codex defer the conversation file until first
  input, so resuming a never-written thread would make the CLI exit with "no
  conversation found".
- `capture_thread_id` is the post-launch id discovery for agents whose id only
  appears after the first persist (Codex polls for its
  `rollout-<ts>-<uuid>.jsonl`); a no-op for agents that pregenerate.

Agents satisfy the contract by mixing in `DefaultLaunchContract` and
overriding the methods their CLI actually supports. The defaults are correct
for an agent with no pane-wrapper launch knobs and no resumable thread
(OpenCode today): no extra flags, no pregenerated id, verbatim resume args,
no on-disk thread to find. The generic transport calls
`registry.get(session.backend)` and dispatches through these methods, so
`tmux/plugin.py` holds no agent literals: it decides whether to spawn the
post-launch id watcher by asking `pregenerate_thread_id() is None`, and
derives which control values to persist on the `SessionRecord` from whether
`launch_flags` actually pins them.

### Transport view

```python
def transport_view(self, runtime: SessionRuntime) -> TransportAdapter:
    ...
```

Returns the `TransportAdapter` (from `waypoint.transports.base`) that handles
`send_input`, `interrupt`, `terminate`, `respond_to_approval`, and
`terminal_snapshot`. Import the transport class lazily inside the method body
— the obvious top-level import path triggers a circular import via the
package's `__init__`.

`send_input(session, text, attachments=None)` takes an optional
`list[ResolvedAttachment]` (a `waypoint.attachments.AttachmentSpec` paired
with its on-disk host path). A backend that sets
`capabilities.supports_attachments` maps these to its native input form:
Claude embeds images as base64 content blocks, Codex sends `localImage`
items, OpenCode adds data-url file parts, and tmux (the universal fallback)
appends the host paths to the text via
`waypoint.attachments.append_attachment_paths` so the wrapped CLI reads them.
Non-image files on image-only protocols degrade to the same path append.
Uploads are persisted server-side by `AttachmentStore` under
`settings.attachments_dir`; the runtime resolves the client-supplied ids to
paths before calling the transport, so a backend never trusts a path from the
client. Blobs are stored under their sanitized original filename,
de-duplicated with ` (1)`, ` (2)` … on collision, so the paths appended for
path-based agents stay legible; the server-issued uuid remains the resolution
key and the only token the client ever sends back.

### Lifecycle

```python
def setup(self, runtime: SessionRuntime) -> None: ...
async def shutdown(self, runtime: SessionRuntime) -> None: ...
def register_routes(self, app: FastAPI, context: Any) -> None: ...

async def restore_session(self, runtime, session) -> None: ...
async def create_session(
    self,
    runtime: SessionRuntime,
    request: SessionCreateRequest,
    *,
    session_id: str,
    launch_target: SshLaunchTargetConfig | None,
    title: str,
    raw_log: Path,
    structured_log: Path,
    git_meta: GitMeta,
    permission_mode: str | None,
    resolved_model: str | None,
    resolved_effort: str | None,
) -> SessionRecord: ...
```

- `setup(runtime)` runs once during `SessionRuntime.__init__`. Build any
  external resources here (Claude resolves its host-side support bundle).
  Resilient to missing prerequisites: log and bail without raising so a
  partial install still boots.
- `shutdown(runtime)` runs from `SessionRuntime.stop` so the plugin can
  close adapters and drain background state in the order it was brought up.
- `register_routes(app, context)` runs once during `create_app`. Mount
  any backend-specific FastAPI routes here. (Claude needs none: tool
  approval rides the `can_use_tool` control protocol over the CLI's stdio
  stream, not an HTTP webhook.)
- `create_session` owns the spawn flow: write the `SessionRecord`, bring up
  the protocol process, swallow startup errors as `HTTPException(400)`, and
  return the persisted record. The runtime has already validated
  `permission_mode` and resolved `resolved_model` / `resolved_effort`.
- `restore_session` runs at startup for sessions that weren't `EXITED` /
  `ERROR` when the runtime stopped, and on user-initiated reattach. Use it to
  reconnect to the protocol process and emit a "session restored" system note.

### Control surface

Each `apply_*` is only invoked when the matching capability flag is `True`.
Plugins that don't support a knob still need the methods because the protocol
is structurally typed — raise `HTTPException(400)` or no-op as appropriate.

```python
async def apply_permission_mode(self, runtime, session, mode: str) -> None: ...
async def apply_model(self, runtime, session, model: str | None) -> None: ...
async def apply_effort(self, runtime, session, effort: str | None) -> bool: ...
def effort_swap_message(self, effort: str | None) -> str: ...
def validate_permission_mode(self, mode: str | None) -> str | None: ...
```

`apply_effort` returns `True` to ask the runtime to publish a system note
describing the swap (Claude's restart-to-pick-up-new-effort path); return
`False` for silent application (Codex's per-turn flag).

### Discovery

```python
async def list_models(self, runtime, launch_target_id=None, include_hidden=False) -> dict: ...
async def list_threads(self, runtime, launch_target_id=None) -> list[Any]: ...
async def import_thread(self, runtime, request) -> SessionRecord: ...
```

`list_models` returns the same payload shape `/api/backends/{id}/models`
serves to the frontend (`{models, default_model_id, default_model_label,
default_effort, supports_free_text}`). `list_threads` returns plugin-specific
summary objects; the API serialises via `model_dump`.

### Slash routing & per-event hooks

```python
async def maybe_handle_input(self, runtime, session, request) -> SessionRecord | None: ...
async def answer_question(self, runtime, session, answer, tool_use_id, answers) -> SessionRecord: ...
async def approve_plan(self, runtime, session, plan_item_id, decision, text) -> SessionRecord: ...
async def post_approval(self, runtime, session) -> None: ...
```

- `maybe_handle_input` runs first on every structured input. Return `None` to
  forward to the transport; return a populated `SessionRecord` to
  short-circuit (Codex's `/compact` slash routes through the
  `thread/compact/start` RPC instead of stdin).
- `answer_question` handles the AskUserQuestion tool. Plugins that don't
  support it raise `HTTPException(400)`.
- `approve_plan` applies a plan-approval decision; `post_approval` runs after
  any structured approval response (Claude syncs the permission-mode pill when
  an ExitPlanMode approval flips the mode out of "plan").

## The claude_tty composition

`claude_tty` is the clearest illustration of the (agent, transport) split: it
is the Claude agent driven over a tty-tail transport. The plugin composes
rather than inherits —

- a `ClaudeCodePlugin` instance (never `setup()`, so no structured adapter is
  built) supplies the agent knowledge: permission-mode catalogue, effort-swap
  note, the account rate-limit probe, and the conversation-file lookup. Its
  capability catalogues are referenced directly
  (`ClaudeCodePlugin.capabilities.permission_modes`, `.effort_levels`,
  `.slash_commands`) so the two backends cannot drift.
- a `TmuxPlugin` instance supplies the shared pane-wrapper transport: session
  teardown and the rate-limit refresh watcher.

The transport-specific lifecycle — tmux create/restore/fork, the
restart-with-resume control swaps, the transcript tailer, and the
AskUserQuestion dialog — lives on `claude_tty` itself.

`claude_tty` applies model, effort, and permission-mode swaps by relaunching
the pane with `claude --resume <thread>` plus the selected `--model`,
`--effort`, and `--permission-mode` flags. The TUI has no in-process knob, so
a swap on a running session interrupts the live turn before resuming; the
plugin sets `settings_change_interrupts_turn` so the composer warns first.
That is deliberately different from `claude_code`, which sends model and
permission changes over Claude's stdio control protocol mid-turn and uses
restart-with-resume only for effort changes. Thread discovery/import reads the
same `~/.claude/projects` transcript store as `claude_code`, so the same
Claude threads appear under both backend ids.

`AskUserQuestion` needs special handling because the TUI withholds the
tool_use record from the transcript until the popup is resolved, so the
question is invisible to the tailer while it blocks the turn. The dialog
poller detects the question screen and sends `Esc`, which makes the TUI flush
the full structured `questions` record (and a "user rejected" result) to the
JSONL. The normalizer, armed by the poller, surfaces that record as a
`WAITING_INPUT` `AskUserQuestion` tool_call — the same card the frontend
renders for `claude_code` — and swallows the rejection so the card stays
answerable. `answer_question` then delivers the answer as an ordinary user
turn (the pane is back at the ready prompt), the way `claude_code` carries it
on a denied tool, and emits a synthetic tool_result so the card resolves to
answered.

## The event envelope

Every persisted event carries `metadata.version: 1` (stamped in
`storage.append_event`). The canonical schema is `EventEnvelope` in
[`backends/events.py`](../backend/src/waypoint/backends/events.py),
with these fields:

```python
class EventEnvelope(BaseModel):
    kind: EventKind
    text: str
    status: SessionStatus | None
    item: ItemPayload | None       # tool_call/tool_result/agent_output
    approval: ApprovalPayload | None
    metadata_version: Literal[1]
    extra: dict[str, Any]          # backend-private overflow
```

Per-plugin `normalize.py` modules turn raw protocol events into the envelope
shape. The frontend's `lib/events.ts::parseEvent` reads the envelope back out
— components no longer poke `metadata.tool_name` etc. directly. When an agent
bumps its protocol, the change lands inside that agent's `normalize.py`; the
frontend reader stays put.

## Frontend catalog

`/api/backends` returns every registered plugin's id, label, badges, and
capability descriptor. `/api/me.backends` carries the same payload so the
bootstrap can hydrate the picker without a second round-trip.

The frontend consumes it via:

- `frontend/src/lib/backends.ts::useBackendCatalog()` — React hook fed by
  `MeResponse.backends`, falls back to `/api/backends` when rendering before
  login.
- `humaniseBackend(id)`, `transportLabel(transport)`, `fidelityFor(transport)`,
  `supportsResume(transport)`, `supportsStructuredApproval(transport)`,
  `permissionModesFor(backend)`, `permissionModeLabel(backend, value)` — all
  accept an optional `BackendCatalog` and fall back to a hand-mirrored map of
  the built-ins so pre-bootstrap callers still render sensibly.

`Backend` and `SessionTransport` are plain `string` aliases, not closed
unions. Adding a backend doesn't require a TypeScript edit.

## Adding a new agent

Worked example: shipping a hypothetical OpenCode agent with its own
structured transport.

### 1. Create the package

```
backend/src/waypoint/backends/opencode/
├── __init__.py            # re-exports OpenCodePlugin
├── plugin.py              # the BackendPlugin + AgentLaunchContract impl
├── adapter.py             # protocol driver (process / WS / SDK client)
├── transport.py           # TransportAdapter impl
├── normalize.py           # raw event → EventEnvelope
└── permission_modes.py    # if applicable
```

Look at `backends/codex/` for a model with a structured streaming adapter and
live model discovery, or `backends/tmux/` for a heuristic-only fallback that
opts out of every inline knob.

### 2. Implement the contracts

Each method has a default in either Claude's or Codex's plugin to crib from.
Mix in `DefaultLaunchContract` and override only the `AgentLaunchContract`
methods your CLI supports. The smallest viable plugin (no model/thread/
permission support, acts like the tmux fallback) only needs:

- `id`, `transport_id`, `label`, `capabilities` (with the relevant
  `supports_*` flags `False`)
- `transport_view`
- `restore_session` (idempotent reconnect)
- `create_session` (the one method that must do real work)
- `setup` / `shutdown` / `register_routes` defaulting to no-ops
- `apply_permission_mode` / `apply_model` / `apply_effort` raising
  `HTTPException(400)` to match the disabled flags
- `effort_swap_message`, `validate_permission_mode`, `list_models`,
  `list_threads`, `import_thread`, `answer_question`, `approve_plan`,
  `maybe_handle_input`, `post_approval` — all no-op / raise-as-appropriate
- the `AgentLaunchContract` methods left at their `DefaultLaunchContract`
  defaults unless the agent is also driven by a generic transport (see below)

### 3. Register it

For built-in plugins (those that ship inside the waypoint package), edit
[`backends/bootstrap.py::build_default_registry`](../backend/src/waypoint/backends/bootstrap.py):

```python
def build_default_registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register(ClaudeCodePlugin())
    registry.register(ClaudeTtyPlugin())
    registry.register(CodexPlugin())
    registry.register(OpenCodePlugin())
    registry.register(TmuxPlugin())
    _register_entry_point_plugins(registry)
    return registry
```

For **third-party** plugins shipped as a separate Python package, publish a
`waypoint.backends` entry point in your `pyproject.toml` instead of editing
waypoint:

```toml
[project.entry-points."waypoint.backends"]
opencode = "waypoint_opencode:build_plugin"
```

The value resolves to either a callable that returns a `BackendPlugin`
instance or a plugin instance directly. Discovery runs once at registry build
(process startup); failures during `load()` are logged and skipped so a broken
external plugin can't take the runtime down.

Either path: the runtime's session lifecycle, API endpoints, scheduler
validation, and frontend picker all pick the plugin up at the next process
restart.

### 4. (Optional) Add launch-target plumbing

If your agent needs SSH-launched sessions, three pieces are involved:

1. [`launch_targets.py`](../backend/src/waypoint/launch_targets.py) stays
   plugin-agnostic — it owns `SshLaunchTargetConfig` and a single
   `plugin_configs: dict[plugin_id, PluginLaunchTargetConfig]` mapping
   dispatched at validation time to each plugin's `launch_target_schema`.
   Presence of a key means "this target supports the plugin"; omitting
   `plugin_configs` entirely defaults to every registered non-fallback plugin.
2. Each plugin declares its own
   `launch_target_schema: type[PluginLaunchTargetConfig]`. Plugins with no
   per-target knobs beyond `remote_bin` (Claude, tmux) point at the base
   class; Codex extends it with `config_overrides` for the `--config K=V`
   flag. The validator parses each per-target block against the matching
   plugin's schema and exposes typed instances via
   `target.plugin_config(plugin_id)`.
3. Backend-specific remote-launch builders live next to the plugin in
   `backends/<id>/remote.py` — see
   [`backends/codex/remote.py`](../backend/src/waypoint/backends/codex/remote.py)
   and
   [`backends/claude_code/remote.py`](../backend/src/waypoint/backends/claude_code/remote.py).
   The plugin's `remote_executable(launch_target)` reads
   `launch_target.remote_bin_for(self.id, self.capabilities.cli_binary)`.

## Adding a new transport

A transport is "how an agent is driven". There are two shapes.

**A native transport for one agent** is just a `TransportAdapter`
implementation returned from that agent's `transport_view`. It owns
`send_input` / `interrupt` / `terminate` / `respond_to_approval` /
`terminal_snapshot` against whatever channel the agent speaks (a subprocess
stream, a websocket, an SDK client). No registry change is needed — it ships
inside the agent package (`backends/<id>/transport.py`).

**A transport registered as its own plugin** — the generic `tmux` pane wrapper
that drives any agent, or an agent-bound tail like `claude_tty` for the Claude
agent — has a distinct `transport_id`. It does not duplicate agent knowledge:

1. Subclass or compose the shared `TmuxTransport` (input injection,
   pipe-pane logging) for the channel mechanics.
2. For everything agent-specific at launch time — model/effort/permission
   flags, the pregenerated or post-launch thread id, the resume form, and the
   conversation-file check — call the agent through the `AgentLaunchContract`
   rather than branching on the agent id. The `tmux` plugin resolves the agent
   with `registry.get(session.backend)` and dispatches the contract; that is
   why it carries no agent literals.
3. For a transport bound to a *specific* agent (the `claude_tty` shape),
   compose that agent's plugin instance for its catalogues and probes — do not
   re-import its internals — and compose a `TmuxPlugin` instance for the shared
   pane-wrapper infrastructure. Register the composed plugin in
   `build_default_registry`.

If a generic transport should be the managed-launch fallback (used when a
structured agent's adapter isn't ready), set
`is_fallback_for_managed_launch=True` on exactly one registered plugin — today
that is `tmux`.

### Test it

A minimum smoke pass:

```bash
cd backend && uv run pytest -q
cd backend && uv run mypy src
cd backend && uv run ruff check src tests
cd frontend && npx tsc --noEmit
cd frontend && npm run lint
```

Then a manual flow:

1. `waypointctl restart`
2. Open the frontend; the new backend appears in the picker on the launch
   panel and the `humaniseBackend` fallback renders its label.
3. Launch a session. Verify events arrive with `metadata.version=1` and your
   `EventKind`s land on the right transcript card.

## Bumping an existing backend

When Claude or Codex ships a new protocol message:

1. Add a branch in the relevant `backends/<id>/normalize.py` —
   `map_notification` (Codex) or the `format_*` helpers (Claude).
2. If the change introduces a new control knob, extend `BackendCapabilities`
   (and place the field on the matching axis — `AgentCapabilities` or
   `TransportCapabilities` — so the split stays a total cover) and the matching
   `apply_*` method on the plugin. The frontend's `useBackendCatalog` and
   `BackendDescriptor` types pick it up via the next `/api/backends` payload.
   The capability golden test pins that payload, so regenerate its fixture
   deliberately when the contract intentionally changes.
3. Update `README.md`'s "Supported agent versions" table to reflect the new
   tested release.

## File-pointer reference

- [`backend/src/waypoint/backends/base.py`](../backend/src/waypoint/backends/base.py)
  — the `BackendPlugin` Protocol, the `AgentLaunchContract` Protocol, and the
  `DefaultLaunchContract` mixin.
- [`backend/src/waypoint/backends/capabilities.py`](../backend/src/waypoint/backends/capabilities.py)
  — `BackendCapabilities` (flat compat aggregate), `AgentCapabilities`,
  `TransportCapabilities`, `PermissionModeSpec`, `SlashCommandSpec`,
  `ModelSource`.
- [`backend/src/waypoint/backends/events.py`](../backend/src/waypoint/backends/events.py)
  — `EventEnvelope` schema.
- [`backend/src/waypoint/backends/registry.py`](../backend/src/waypoint/backends/registry.py)
  — `BackendRegistry` and `get_registry()`.
- [`backend/src/waypoint/backends/bootstrap.py`](../backend/src/waypoint/backends/bootstrap.py)
  — `build_default_registry()` is the registration entry point.
- [`backend/src/waypoint/backends/claude_code/plugin.py`](../backend/src/waypoint/backends/claude_code/plugin.py)
  — full reference agent (static models, stdio control-protocol approval,
  support bundle, launch contract).
- [`backend/src/waypoint/backends/claude_tty/plugin.py`](../backend/src/waypoint/backends/claude_tty/plugin.py)
  — the Claude agent composed over the tty-tail transport.
- [`backend/src/waypoint/backends/codex/plugin.py`](../backend/src/waypoint/backends/codex/plugin.py)
  — full reference agent (live model RPC, slash routing, post-launch id capture).
- [`backend/src/waypoint/backends/tmux/plugin.py`](../backend/src/waypoint/backends/tmux/plugin.py)
  — the generic pane wrapper that dispatches the launch contract with no agent
  literals.
- [`frontend/src/lib/backends.ts`](../frontend/src/lib/backends.ts)
  — the catalog hook and capability fallbacks.
- [`frontend/src/lib/events.ts`](../frontend/src/lib/events.ts)
  — `parseEvent` envelope reader.
```
