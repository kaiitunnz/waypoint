# Coding-agent backend plugins

Waypoint orchestrates coding agents — Claude Code, Codex, OpenCode, future ones —
through a plugin registry. The runtime, API, storage, and frontend
catalog all dispatch by plugin id; adding a new backend means writing a
plugin module, not editing the core. This doc covers the design, the
contract each plugin must satisfy, and a recipe for shipping a new
backend.

## Goals

- One place per backend for everything backend-specific: lifecycle,
  control surface, model/thread discovery, transport, event
  normalization, route registration.
- The runtime stays generic. No `if backend == "codex"` branches.
- The frontend reads a live catalog (`/api/backends`) so picker
  dropdowns, badge palettes, permission-mode lists, and slash-command
  hints update without TypeScript edits.
- New protocol-version bumps for an existing backend stay inside that
  backend's module.

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
    ├── base.py          ← BackendPlugin Protocol (the contract)
    ├── capabilities.py  ← BackendCapabilities + PermissionModeSpec etc.
    ├── events.py        ← EventEnvelope (versioned metadata schema)
    ├── registry.py      ← BackendRegistry, get_registry()
    ├── bootstrap.py     ← build_default_registry() — registration entry
    ├── claude_code/
    │   ├── plugin.py
    │   ├── adapter.py        ← stream-json subprocess driver
    │   ├── transport.py      ← TransportAdapter impl
    │   ├── normalize.py      ← stream-json → EventEnvelope helpers
    │   ├── permission_modes.py
    │   ├── models.py         ← static model alias table
    │   ├── support.py        ← host-side support bundle (thread enumerator)
    │   ├── threads.py        ← local thread enumeration
    │   └── threads_remote.py ← SSH thread enumeration
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
    └── tmux/
        ├── plugin.py
        ├── adapter.py
        ├── transport.py
        └── normalize.py      ← terminal-text scrape (was normalizer.py)
```

## The plugin contract

A backend plugin is any object that satisfies the
`waypoint.backends.base.BackendPlugin` Protocol. The full surface lives
in [`backend/src/waypoint/backends/base.py`](../backend/src/waypoint/backends/base.py);
this section walks through it method by method.

### Identity + capabilities (class attributes)

```python
class MyPlugin:
    id: str                     # registry key + storage column value, e.g. "opencode"
    transport_id: str           # storage column value, e.g. "opencode_ws"
    label: str                  # human-readable, surfaced in pickers
    capabilities: BackendCapabilities
```

`BackendCapabilities` (frozen dataclass; see
[`capabilities.py`](../backend/src/waypoint/backends/capabilities.py))
declares everything the runtime/frontend needs to reason about the
plugin without calling into it:

| Field | Type | What the runtime/frontend does with it |
|-------|------|----------------------------------------|
| `is_structured` | `bool` | `False` flips the frontend transcript to the heuristic terminal-scrape renderer; `True` enables structured tool-pair cards. |
| `supports_resume` | `bool` | Enables the Resume button on detached sessions (tmux). |
| `supports_terminate` | `bool` | Reserved; today every plugin returns `True`. |
| `supports_set_model_inline` | `bool` | Gates the model picker in the composer + the `/api/sessions/{id}/model` endpoint. |
| `supports_set_effort_inline` | `bool` | Gates the effort picker. Set `False` if effort changes require a session restart (Claude); the runtime still routes through your `apply_effort`, which decides whether to short-circuit. |
| `supports_set_permission_mode_inline` | `bool` | Gates the permission-mode picker + the `/api/sessions/{id}/mode` endpoint. |
| `supports_thread_discovery` | `bool` | Enables `GET /api/backends/{id}/threads`. |
| `supports_thread_import` | `bool` | Enables `POST /api/backends/{id}/sessions/import`. |
| `supports_slash_compact` | `bool` | Frontend hint that `/compact` is meaningful for this backend. |
| `permission_modes` | `tuple[PermissionModeSpec, ...]` | Drives the picker, scheduler validation, and the catalog payload. |
| `effort_levels` | `tuple[str, ...]` | Empty tuple means "discovered per-model from the live model list" (Codex). |
| `model_source` | `ModelSource` | `STATIC` (Claude — config + alias table), `LIVE_RPC` (Codex — `model/list`), `NONE` (tmux fallback). |
| `slash_commands` | `tuple[SlashCommandSpec, ...]` | Frontend slash-suggestion list. |
| `approval_decisions` | `tuple[str, ...]` | Buttons rendered on the structured approval card. |
| `badges` | `dict[str, str]` | UI palette: `{"glyph": "X", "color": "#34d399"}`. |
| `cli_binary` | `str \| None` | Default CLI invoked for local launches and for tmux fallback launches; `None` opts out. Override per-deployment via `plugin_configs.<id>.local_bin` (local) or `ssh_targets[*].plugin_configs.<id>.remote_bin` (per SSH target). |
| `target_aliases` | `tuple[str, ...]` | Substrings used to infer this backend from a tmux pane name. |

### Transport view

```python
def transport_view(self, runtime: SessionRuntime) -> TransportAdapter:
    ...
```

Returns the `TransportAdapter` (from
`waypoint.transports.base`) that handles `send_input`, `interrupt`,
`terminate`, `respond_to_approval`, and `terminal_snapshot`. Import the
transport class lazily inside the method body — the obvious top-level
import path triggers a circular import via the package's `__init__`.

### Lifecycle

```python
def setup(self, runtime: SessionRuntime) -> None: ...
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
- `register_routes(app, context)` runs once during `create_app`. Mount
  any backend-specific FastAPI routes here. (Claude needs none: tool
  approval rides the `can_use_tool` control protocol over the CLI's stdio
  stream, not an HTTP webhook.)
- `create_session` owns the spawn flow: write the `SessionRecord`,
  bring up the protocol process, swallow startup errors as
  `HTTPException(400)`, and return the persisted record. The runtime
  has already validated `permission_mode` and resolved `resolved_model`
  / `resolved_effort` from settings.
- `restore_session` runs at startup for sessions that weren't `EXITED`
  / `ERROR` when the runtime stopped. Use it to reconnect to your
  protocol process and emit a "session restored" system note.

### Control surface

Each `apply_*` is only invoked when the matching capability flag is
`True`. Plugins that don't support a knob still need the methods
because the protocol is structurally typed — raise `HTTPException(400)`
or no-op as appropriate.

```python
async def apply_permission_mode(self, runtime, session, mode: str) -> None: ...
async def apply_model(self, runtime, session, model: str | None) -> None: ...
async def apply_effort(self, runtime, session, effort: str | None) -> bool: ...
def effort_swap_message(self, effort: str | None) -> str: ...
def validate_permission_mode(self, mode: str | None) -> str | None: ...
```

`apply_effort` returns `True` to ask the runtime to publish a system
note describing the swap (Claude's restart-to-pick-up-new-effort path);
return `False` for silent application (Codex's per-turn flag).

### Discovery

```python
async def list_models(self, runtime, launch_target_id=None, include_hidden=False) -> dict: ...
async def list_threads(self, runtime, launch_target_id=None) -> list[Any]: ...
async def import_thread(self, runtime, request) -> SessionRecord: ...
```

`list_models` returns the same payload shape `/api/backends/{id}/models`
serves to the frontend (`{models, default_model_id, default_model_label, default_effort,
supports_free_text}`). `list_threads` returns plugin-specific summary
objects; the API serialises via `model_dump`.

### Slash routing & per-event hooks

```python
async def maybe_handle_input(self, runtime, session, request) -> SessionRecord | None: ...
async def answer_question(self, runtime, session, answer, tool_use_id, answers) -> SessionRecord: ...
async def post_approval(self, runtime, session) -> None: ...
```

- `maybe_handle_input` runs first on every structured input. Return
  `None` to forward to the transport; return a populated `SessionRecord`
  to short-circuit (Codex's `/compact` slash routes through the
  `thread/compact/start` RPC instead of stdin).
- `answer_question` handles the AskUserQuestion tool. Plugins that
  don't support it raise `HTTPException(400)`.
- `post_approval` runs after a structured approval response. Claude
  uses it to sync the permission-mode pill when an ExitPlanMode
  approval flips the mode out of "plan".

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

Per-plugin `normalize.py` modules turn raw protocol events into the
envelope shape. The frontend's `lib/events.ts::parseEvent` reads the
envelope back out — components no longer poke `metadata.tool_name`
etc. directly.

When a backend bumps its protocol, the change lands inside that
backend's `normalize.py`. The frontend reader stays put.

## Frontend catalog

`/api/backends` returns every registered plugin's id, label, badges,
and capability descriptor. `/api/me.backends` carries the same payload
so the bootstrap can hydrate the picker without a second round-trip.

The frontend consumes it via:

- `frontend/src/lib/backends.ts::useBackendCatalog()` — React hook
  fed by `MeResponse.backends`, falls back to `/api/backends` when
  rendering before login.
- `humaniseBackend(id)`, `transportLabel(transport)`,
  `fidelityFor(transport)`, `supportsResume(transport)`,
  `supportsStructuredApproval(transport)`,
  `permissionModesFor(backend)`, `permissionModeLabel(backend, value)`
  — all accept an optional `BackendCatalog` and fall back to a
  hand-mirrored map of the two built-ins so pre-bootstrap callers
  still render sensibly.

`Backend` and `SessionTransport` are plain `string` aliases, not closed
unions. Adding a backend doesn't require a TypeScript edit.

## Adding a new backend

Worked example: shipping a hypothetical OpenCode backend.

### 1. Create the package

```
backend/src/waypoint/backends/opencode/
├── __init__.py            # re-exports OpenCodePlugin
├── plugin.py              # the BackendPlugin implementation
├── adapter.py             # protocol driver (process / WS / SDK client)
├── transport.py           # TransportAdapter impl
├── normalize.py           # raw event → EventEnvelope
└── permission_modes.py    # if applicable
```

Look at `backends/codex/` for a model with a structured streaming
adapter and live model discovery, or `backends/tmux/` for a
heuristic-only fallback that opts out of every inline knob.

### 2. Implement the protocol

Each method has a default in either Claude's or Codex's plugin to
crib from. The smallest viable plugin (no model/thread/permission
support, acts like the tmux fallback) only needs:

- `id`, `transport_id`, `label`, `capabilities` (with the relevant
  `supports_*` flags `False`)
- `transport_view`
- `restore_session` (idempotent reconnect)
- `create_session` (the one method that must do real work)
- `setup` / `register_routes` defaulting to no-ops
- `apply_permission_mode` / `apply_model` / `apply_effort` raising
  `HTTPException(400)` to match the disabled flags
- `effort_swap_message`, `validate_permission_mode`, `list_models`,
  `list_threads`, `import_thread`, `answer_question`,
  `maybe_handle_input`, `post_approval` — all no-op /
  raise-as-appropriate

### 3. Register it

For built-in plugins (those that ship inside the waypoint package),
edit [`backends/bootstrap.py::build_default_registry`](../backend/src/waypoint/backends/bootstrap.py):

```python
def build_default_registry() -> BackendRegistry:
    registry = BackendRegistry()
    registry.register(ClaudeCodePlugin())
    registry.register(CodexPlugin())
    registry.register(OpenCodePlugin())
    registry.register(TmuxPlugin())
    _register_entry_point_plugins(registry)
    return registry
```

For **third-party** plugins shipped as a separate Python package,
publish a `waypoint.backends` entry point in your `pyproject.toml`
instead of editing waypoint:

```toml
[project.entry-points."waypoint.backends"]
opencode = "waypoint_opencode:build_plugin"
```

The value resolves to either a callable that returns a
`BackendPlugin` instance or a plugin instance directly. Discovery
runs once at registry build (process startup); failures during
``load()`` are logged and skipped so a broken external plugin can't
take the runtime down.

Either path: the runtime's session lifecycle, API endpoints,
scheduler validation, and frontend picker all pick the plugin up at
the next process restart.

### 4. (Optional) Add launch-target plumbing

If your backend needs SSH-launched sessions, three pieces are involved:

1. [`launch_targets.py`](../backend/src/waypoint/launch_targets.py)
   stays plugin-agnostic — it owns `SshLaunchTargetConfig` and a single
   `plugin_configs: dict[plugin_id, PluginLaunchTargetConfig]` mapping
   dispatched at validation time to each plugin's `launch_target_schema`.
   Presence of a key means "this target supports the plugin"; omitting
   `plugin_configs` entirely defaults to every registered non-fallback
   plugin so a minimal target Just Works.
2. Each plugin declares its own `launch_target_schema: type[PluginLaunchTargetConfig]`.
   Plugins with no per-target knobs beyond `remote_bin` (Claude, tmux)
   point at the base class; Codex extends it with `config_overrides`
   for the `--config K=V` flag. The validator on `SshLaunchTargetConfig`
   parses each per-target block against the matching plugin's schema
   and exposes typed instances via `target.plugin_config(plugin_id)`.
3. Backend-specific remote-launch builders live next to the plugin in
   `backends/<id>/remote.py` — see
   [`backends/codex/remote.py`](../backend/src/waypoint/backends/codex/remote.py)
   and
   [`backends/claude_code/remote.py`](../backend/src/waypoint/backends/claude_code/remote.py).
   The plugin's `remote_executable(launch_target)` reads
   `launch_target.remote_bin_for(self.id, self.capabilities.cli_binary)`,
   which is just a convenience over `target.plugin_config(...).remote_bin`.

### 5. Test it

A minimum smoke pass:

```bash
cd backend && uv run pytest -q
cd backend && uv run mypy src tests
cd backend && uv run ruff check src tests
cd frontend && npx tsc --noEmit
cd frontend && npm run lint
```

Then a manual flow:

1. `./scripts/waypoint.sh restart`
2. Open the frontend; the new backend appears in the picker on the
   launch panel and the `humaniseBackend` fallback renders its label.
3. Launch a session. Verify events arrive with `metadata.version=1`
   and your `EventKind`s land on the right transcript card.

## Bumping an existing backend

When Claude or Codex ships a new protocol message:

1. Add a branch in the relevant `backends/<id>/normalize.py` —
   `map_notification` (Codex) or the `format_*` helpers (Claude).
2. If the change introduces a new control knob, extend
   `BackendCapabilities` and the matching `apply_*` method on the
   plugin. The frontend's `useBackendCatalog` and `BackendDescriptor`
   types pick it up via the next `/api/backends` payload.
3. Update `README.md`'s "Supported agent versions" table to reflect
   the new tested release.

## File-pointer reference

- [`backend/src/waypoint/backends/base.py`](../backend/src/waypoint/backends/base.py)
  — the `BackendPlugin` Protocol.
- [`backend/src/waypoint/backends/capabilities.py`](../backend/src/waypoint/backends/capabilities.py)
  — `BackendCapabilities`, `PermissionModeSpec`, `SlashCommandSpec`,
  `ModelSource`.
- [`backend/src/waypoint/backends/events.py`](../backend/src/waypoint/backends/events.py)
  — `EventEnvelope` schema.
- [`backend/src/waypoint/backends/registry.py`](../backend/src/waypoint/backends/registry.py)
  — `BackendRegistry` and `get_registry()`.
- [`backend/src/waypoint/backends/bootstrap.py`](../backend/src/waypoint/backends/bootstrap.py)
  — `build_default_registry()` is the registration entry point.
- [`backend/src/waypoint/backends/claude_code/plugin.py`](../backend/src/waypoint/backends/claude_code/plugin.py)
  — full reference plugin (static models, stdio control-protocol approval,
  support bundle).
- [`backend/src/waypoint/backends/codex/plugin.py`](../backend/src/waypoint/backends/codex/plugin.py)
  — full reference plugin (live model RPC, slash routing).
- [`frontend/src/lib/backends.ts`](../frontend/src/lib/backends.ts)
  — the catalog hook and capability fallbacks.
- [`frontend/src/lib/events.ts`](../frontend/src/lib/events.ts)
  — `parseEvent` envelope reader.
