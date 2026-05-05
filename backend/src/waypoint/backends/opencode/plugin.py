import asyncio
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
    PermissionModeSpec,
    SlashCommandSpec,
)
from waypoint.backends.opencode.adapter import OpenCodeAdapter, OpenCodeError
from waypoint.backends.opencode.health import AdapterHealth
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
from waypoint.git_meta import GitMeta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import (
    EventKind,
    SessionCreateRequest,
    SessionEnvelope,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.opencode")

DEFAULT_OPENCODE_MODEL = "opencode/minimax-m2.5-free"

OPENCODE_PERMISSION_MODES = (
    PermissionModeSpec(
        id="default",
        label="Default",
        description="Use OpenCode's built-in defaults (no rule attached)",
    ),
    PermissionModeSpec(
        id="ask", label="Ask", description="Ask for permission on every action"
    ),
    PermissionModeSpec(
        id="allow", label="Allow", description="Automatically approve actions"
    ),
    PermissionModeSpec(id="deny", label="Deny", description="Deny all actions"),
    PermissionModeSpec(
        id="plan",
        label="Plan",
        description="Draft and save an architecture plan before coding",
    ),
)
OPENCODE_PERMISSION_ACTIONS = {"ask", "allow", "deny"}

OPENCODE_SLASH_COMMANDS = (
    SlashCommandSpec(
        name="compact", description="Compact the session to reduce context"
    ),
    SlashCommandSpec(name="new", description="Start a new session"),
)


def _ruleset_for_mode(mode: str | None) -> list[dict[str, str]] | None:
    # The runtime substitutes "default" when no mode is selected; that means
    # "let OpenCode decide" — don't send a permission key at all.
    if mode not in OPENCODE_PERMISSION_ACTIONS:
        return None
    return [{"permission": "*", "pattern": "*", "action": mode}]


class OpenCodePluginConfig(PluginConfig):
    pass


class OpenCodeThreadImportRequest(BaseModel):
    thread_id: str
    launch_target_id: str | None = None
    cwd: str | None = None


class OpenCodeThreadSummary(BaseModel):
    id: str
    title: str
    directory: str | None = None
    created_at: int | None = None
    updated_at: int | None = None


class OpenCodePlugin:
    id = "opencode"
    transport_id = "opencode_http"
    label = "OpenCode"
    import_request_schema: type[BaseModel] | None = OpenCodeThreadImportRequest
    config_schema: type[PluginConfig] = OpenCodePluginConfig
    launch_target_schema: type[PluginLaunchTargetConfig] = PluginLaunchTargetConfig

    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=False,
        supports_set_model_inline=True,
        supports_set_effort_inline=True,
        supports_set_effort_with_restart=False,
        supports_set_permission_mode_inline=True,
        supports_thread_discovery=True,
        supports_thread_import=True,
        supports_slash_compact=True,
        permission_modes=OPENCODE_PERMISSION_MODES,
        effort_levels=(),
        model_source=ModelSource.LIVE_RPC,
        slash_commands=OPENCODE_SLASH_COMMANDS,
        badges={"glyph": "O", "color": "#cbd5e1"},
        cli_binary="opencode",
        target_aliases=("opencode",),
    )

    def __init__(self) -> None:
        self._adapters: dict[tuple[str | None, str], OpenCodeAdapter] = {}
        # Per-adapter health (cooldown after death + circuit breaker after
        # repeated launch failures). Keyed identically to ``_adapters`` so
        # one entry survives adapter object replacement.
        self._health: dict[tuple[str | None, str], AdapterHealth] = {}
        # Reconnect tasks keyed on adapter key so a single launch_target
        # never has more than one reconnect loop running concurrently.
        self._reconnect_tasks: dict[tuple[str | None, str], asyncio.Task[None]] = {}
        # Sessions tracked by the reconnect loop per adapter key.
        self._reconnect_targets: dict[tuple[str | None, str], set[str]] = {}
        self._lock = asyncio.Lock()
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._shutting_down = False

    def _default_cwd(
        self, runtime: "SessionRuntime", launch_target_id: str | None
    ) -> str:
        if launch_target_id is not None:
            launch_target = runtime._find_launch_target(launch_target_id)
            if launch_target is not None:
                return launch_target.default_cwd
        return str(Path(runtime.settings.default_cwd).expanduser())

    def _adapter_cwd(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        cwd: str | None,
    ) -> str:
        chosen = cwd or self._default_cwd(runtime, launch_target_id)
        if launch_target_id is None:
            chosen = str(Path(chosen).expanduser())
        return os.path.normpath(chosen)

    def _adapter_key(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        cwd: str | None,
    ) -> tuple[str | None, str]:
        return launch_target_id, self._adapter_cwd(runtime, launch_target_id, cwd)

    def _find_adapter_for_launch_target(
        self, launch_target_id: str | None
    ) -> OpenCodeAdapter | None:
        for (adapter_launch_target_id, _), adapter in self._adapters.items():
            if adapter_launch_target_id == launch_target_id:
                return adapter
        return None

    def _adapters_for_launch_target(
        self, launch_target_id: str | None
    ) -> list[OpenCodeAdapter]:
        return [
            adapter
            for (adapter_launch_target_id, _), adapter in self._adapters.items()
            if adapter_launch_target_id == launch_target_id
        ]

    def _require_adapter(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        cwd: str | None = None,
    ) -> OpenCodeAdapter:
        adapter = self._adapters.get(self._adapter_key(runtime, launch_target_id, cwd))
        if adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "opencode adapter for target "
                    f"{launch_target_id} and cwd {cwd} not initialized"
                ),
            )
        return adapter

    async def _handle_agent_changed(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        old_agent: str | None,
        new_agent: str | None,
    ) -> None:
        # If OpenCode automatically switched from the plan agent back to the build agent
        # (e.g. after the user approved the plan via a question.replied event), we must
        # sync Waypoint's permission_mode state so the UI drops out of plan mode.
        if old_agent == "plan" and new_agent != "plan":
            session = runtime.get_session(session_id)
            if session.permission_mode == "plan":
                try:
                    adapter = self._require_adapter(
                        runtime, session.launch_target_id, session.cwd
                    )
                    target_mode = adapter.get_pre_plan_mode(session.id) or "default"
                except HTTPException:
                    target_mode = "default"

                if target_mode == "plan":
                    target_mode = "default"

                runtime.storage.update_session(session_id, permission_mode=target_mode)
                # Drop the persisted plan-agent footprint so a later restore
                # doesn't re-enter plan mode.
                self._persist_transport_state(
                    runtime, session, agent=None, pre_plan_mode=None
                )

                # Emit system note so transcript shows that plan mode exited
                await runtime._emit_adapter_event(
                    session_id,
                    EventKind.SYSTEM_NOTE,
                    "Exited plan mode",
                    {"status": SessionStatus.RUNNING},
                    SessionStatus.RUNNING,
                )

                await runtime.broadcast.publish(
                    SessionEnvelope(
                        type="session_list_update",
                        payload={
                            "sessions": [
                                item.model_dump(mode="json")
                                for item in runtime.list_sessions()
                            ]
                        },
                    )
                )

    def _health_for(self, key: tuple[str | None, str]) -> AdapterHealth:
        if key not in self._health:
            self._health[key] = AdapterHealth()
        return self._health[key]

    async def _get_or_create_adapter(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        cwd: str | None,
        *,
        user_initiated: bool = False,
    ) -> OpenCodeAdapter:
        async with self._lock:
            key = self._adapter_key(runtime, launch_target_id, cwd)
            if key in self._adapters:
                return self._adapters[key]

            health = self._health_for(key)
            allowed, reason = health.can_attempt(user_initiated=user_initiated)
            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=reason or "opencode adapter temporarily unavailable",
                )

            launch_target = None
            if launch_target_id is not None:
                launch_target = runtime._find_launch_target(launch_target_id)
                if launch_target is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"unknown launch target: {launch_target_id}",
                    )

            def _on_agent_changed(sid: str, old: str | None, new: str | None) -> None:
                if self._shutting_down:
                    return
                task = asyncio.create_task(
                    self._handle_agent_changed(runtime, sid, old, new)
                )
                self._pending_tasks.add(task)
                task.add_done_callback(self._pending_tasks.discard)

            def _on_server_died(active_session_ids: list[str]) -> None:
                self._handle_server_died(runtime, key, active_session_ids)

            adapter = OpenCodeAdapter(
                emit_event=runtime._emit_adapter_event,
                launch_target=launch_target,
                on_agent_changed=_on_agent_changed,
                on_server_died=_on_server_died,
                workdir=key[1],
            )
            self._adapters[key] = adapter
            return adapter

    def _handle_server_died(
        self,
        runtime: "SessionRuntime",
        key: tuple[str | None, str],
        active_session_ids: list[str],
    ) -> None:
        # Synchronous bridge from the adapter's `_on_server_died` into the
        # plugin's health + reconnect machinery. Keep this fast: it runs
        # inside the adapter's teardown path.
        if self._shutting_down:
            return
        self._health_for(key).record_death()
        targets = self._reconnect_targets.setdefault(key, set())
        targets.update(active_session_ids)
        self._ensure_reconnect_task(runtime, key)

    def _ensure_reconnect_task(
        self,
        runtime: "SessionRuntime",
        key: tuple[str | None, str],
    ) -> None:
        existing = self._reconnect_tasks.get(key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._reconnect_loop(runtime, key),
            name=f"opencode-reconnect-{key}",
        )
        self._reconnect_tasks[key] = task
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        task.add_done_callback(lambda _t: self._reconnect_tasks.pop(key, None))

    async def _reconnect_loop(
        self,
        runtime: "SessionRuntime",
        key: tuple[str | None, str],
    ) -> None:
        # Capped exponential backoff that loops forever until either every
        # tracked session is gone (user deleted them) or the plugin is
        # shutting down. The cap is intentionally low (5 minutes) so a
        # VPN coming back hours later still recovers within five minutes
        # of the link healing.
        backoff_schedule = [5, 10, 30, 60, 120, 300]
        attempt = 0
        while not self._shutting_down:
            targets = self._reconnect_targets.get(key, set())
            if not targets:
                return
            try:
                # The loop is the dedicated retry mechanism — its own
                # backoff schedule already paces attempts, so it must
                # bypass the cooldown/quarantine gate. Without this,
                # `record_death()` having just fired means the very
                # first iteration is gated as a "failure", inflating
                # `consecutive_failures` toward quarantine without ever
                # really touching SSH. The gate exists to fail-fast
                # passive HTTP callers, not the loop.
                adapter = await self._get_or_create_adapter(
                    runtime, key[0], key[1] or None, user_initiated=True
                )
                await adapter.start()
                health = self._health_for(key)
                health.record_success()
            except Exception as exc:
                self._health_for(key).record_failure()
                wait = backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
                attempt += 1
                log.warning(
                    "opencode reconnect for %s failed (%s); retrying in %ds",
                    key,
                    exc,
                    wait,
                )
                try:
                    await asyncio.sleep(wait)
                except asyncio.CancelledError:
                    raise
                continue

            attempt = 0
            await self._restore_after_reconnect(runtime, key)

            # Successful pass: drain tracked targets and exit. If new
            # deaths happen, `_handle_server_died` re-arms a fresh loop.
            self._reconnect_targets.pop(key, None)
            return

    async def _restore_after_reconnect(
        self,
        runtime: "SessionRuntime",
        key: tuple[str | None, str],
    ) -> None:
        targets = list(self._reconnect_targets.get(key, set()))
        for session_id in targets:
            session = runtime.storage.get_session(session_id)
            if session is None:
                self._reconnect_targets.get(key, set()).discard(session_id)
                continue
            adapter = self._adapters.get(key)
            if adapter is None:
                # Slot was wiped between loop start and here; bail and let
                # the next death event re-arm the loop.
                return
            opencode_session_id = session.transport_state.get("opencode_session_id")
            if not opencode_session_id:
                self._reconnect_targets.get(key, set()).discard(session_id)
                continue
            try:
                await adapter.restore_session(
                    session.id,
                    session.cwd,
                    opencode_session_id,
                    model=session.model,
                    agent=session.transport_state.get("agent"),
                    effort=session.effort,
                )
            except Exception as exc:
                log.warning(
                    "opencode resurrect of %s failed after reconnect: %s",
                    session.id,
                    exc,
                )
                # Leave session ERROR; user can retry via /reattach.
                continue
            runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
            await runtime._record_system_event(
                session.id,
                "OpenCode connection restored",
                status=SessionStatus.IDLE,
            )

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        from waypoint.backends.opencode.transport import OpenCodeTransport

        return OpenCodeTransport(runtime, self)

    def setup(self, runtime: "SessionRuntime") -> None:
        log.info("setting up opencode plugin")

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        self._shutting_down = True
        pending = list(self._pending_tasks)
        self._pending_tasks.clear()
        for task in pending:
            task.cancel()
        if pending:
            # Await the cancelled tasks so a late wake-up cannot fire after
            # the adapter (and its storage handle) is gone.
            await asyncio.gather(*pending, return_exceptions=True)
        for adapter in list(self._adapters.values()):
            await adapter.shutdown()
        self._adapters.clear()

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        return True

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        return launch_target.remote_bin_for(self.id, self.capabilities.cli_binary) or ""

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        key = self._adapter_key(runtime, session.launch_target_id, session.cwd)
        # Drop this session from any in-flight reconnect-loop target set so
        # an explicit terminate can't be silently undone by a later loop
        # tick resurrecting it.
        targets = self._reconnect_targets.get(key)
        if targets is not None:
            targets.discard(session.id)
            if not targets:
                self._reconnect_targets.pop(key, None)
        adapter = self._adapters.get(key)
        if adapter is not None:
            await adapter.terminate_session(session.id)

    def clear_health_for_user_retry(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Hook surfaced via getattr from `runtime._reattach_session`. Clears
        # the cooldown / circuit breaker so the user-initiated reattach
        # bypasses backoff that the auto-reconnect loop is already
        # observing. Also re-arms a fresh reconnect attempt: cancel any
        # active loop so the next `_get_or_create_adapter` call drives
        # the SSH spinup synchronously instead of racing the loop.
        key = self._adapter_key(runtime, session.launch_target_id, session.cwd)
        health = self._health.get(key)
        if health is not None:
            health.record_success()
        existing = self._reconnect_tasks.get(key)
        if existing is not None and not existing.done():
            existing.cancel()

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        if self._shutting_down:
            return
        adapter = self._adapters.get(
            self._adapter_key(runtime, session.launch_target_id, session.cwd)
        )
        if adapter is not None:
            task = asyncio.create_task(adapter.terminate_session(session.id))
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    def register_routes(self, app: FastAPI, context: Any) -> None:
        pass

    def validate_permission_mode(self, mode: str | None) -> str | None:
        if mode is None or mode == "":
            return None
        # "default" is a real, user-selectable mode for OpenCode — it clears
        # any attached ruleset so the upstream defaults apply. We pass it
        # through so set_permission_mode round-trips it (the runtime rejects
        # `None`), and apply_permission_mode handles it via _ruleset_for_mode.
        if mode == "default":
            return "default"
        if mode == "plan":
            return "plan"
        if mode not in OPENCODE_PERMISSION_ACTIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unsupported opencode permission mode: {mode}; "
                    f"expected one of {', '.join(sorted(OPENCODE_PERMISSION_ACTIONS))} or plan"
                ),
            )
        return mode

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        adapter = self._require_adapter(runtime, session.launch_target_id, session.cwd)

        if mode == "plan":
            pre_plan_mode = (
                session.permission_mode
                if session.permission_mode != "plan"
                else session.transport_state.get("pre_plan_mode")
            )
            await adapter.set_pre_plan_mode(session.id, pre_plan_mode)
            await adapter.set_agent(session.id, "plan")
            # OpenCode's plan agent applies its own permissions natively,
            # so we drop to default ruleset for the session
            ruleset = _ruleset_for_mode("default") or []
            self._persist_transport_state(
                runtime, session, agent="plan", pre_plan_mode=pre_plan_mode
            )
        else:
            await adapter.set_agent(session.id, None)
            ruleset = _ruleset_for_mode(mode) or []
            self._persist_transport_state(
                runtime, session, agent=None, pre_plan_mode=None
            )

        success = await adapter.set_session_permission(session.id, ruleset)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to set permission mode to {mode}",
            )

    def _persist_transport_state(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        **updates: Any,
    ) -> None:
        merged = {**session.transport_state, **updates}
        session.transport_state = merged
        runtime.storage.update_session(session.id, transport_state=merged)

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        if model is None:
            return
        adapter = self._require_adapter(runtime, session.launch_target_id, session.cwd)
        success = await adapter.set_model(session.id, model)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to set model to {model}",
            )

    async def apply_effort(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        effort: str | None,
    ) -> bool:
        adapter = self._require_adapter(runtime, session.launch_target_id, session.cwd)
        success = await adapter.set_effort(session.id, effort)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to set effort to {effort}",
            )
        # OpenCode applies effort inline on the next prompt — no restart needed.
        return False

    def effort_swap_message(self, effort: str | None) -> str:
        # Never published — apply_effort returns False so the runtime skips
        # the announcement path entirely.
        return ""

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        adapters = list(reversed(self._adapters_for_launch_target(launch_target_id)))
        if not adapters:
            adapters = [
                await self._get_or_create_adapter(runtime, launch_target_id, None)
            ]

        last_error: Exception | None = None
        fallback: tuple[list[dict[str, Any]], dict[str, Any]] | None = None
        for adapter in adapters:
            try:
                providers = await adapter.list_providers()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
            models = self._flatten_provider_models(
                providers, include_hidden=include_hidden
            )
            if models:
                default_model_id, default_model_label = self._select_default_model(
                    models, providers
                )
                return {
                    "backend": self.id,
                    "models": models,
                    "default_model_id": default_model_id,
                    "default_model_label": default_model_label,
                    "default_effort": None,
                    "supports_free_text": True,
                }
            fallback = (models, providers)

        if fallback is not None:
            models, providers = fallback
            default_model_id, default_model_label = self._select_default_model(
                models, providers
            )
            return {
                "backend": self.id,
                "models": models,
                "default_model_id": default_model_id,
                "default_model_label": default_model_label,
                "default_effort": None,
                "supports_free_text": True,
            }

        if last_error is not None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"failed to list opencode providers: {last_error}",
            ) from last_error

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="failed to list opencode providers: no adapters available",
        )

    def _flatten_provider_models(
        self,
        providers: dict[str, Any],
        *,
        include_hidden: bool,
    ) -> list[dict[str, Any]]:
        # `/provider` returns every provider in the models.dev manifest, but
        # OpenCode only resolves models from providers it has actually
        # *connected* (env API key, stored auth, or auto-loaded). Listing
        # the rest leads the user into picking a model that the runtime
        # rejects with "Model not found" mid-prompt. Filter when the key
        # is present; if it's missing entirely (older server payloads or
        # tests), fall back to listing everything.
        raw_connected = providers.get("connected")
        connected: set[str] | None
        if isinstance(raw_connected, list):
            connected = {
                item for item in raw_connected if isinstance(item, str) and item
            }
        else:
            connected = None
        flattened: list[dict[str, Any]] = []
        for provider in providers.get("all", []) or []:
            if not isinstance(provider, dict):
                continue
            provider_id = provider.get("id")
            if not isinstance(provider_id, str) or not provider_id:
                continue
            if connected is not None and provider_id not in connected:
                continue
            provider_label = provider.get("name") or provider_id
            for model_id, model in (provider.get("models") or {}).items():
                if not isinstance(model_id, str) or not model_id:
                    continue
                if not isinstance(model, dict):
                    continue
                if not include_hidden and model.get("status") == "deprecated":
                    continue
                model_label = model.get("name") or model_id

                supported_efforts = []
                variants = model.get("variants")
                if isinstance(variants, dict):
                    supported_efforts = list(variants.keys())

                flattened.append(
                    {
                        "id": f"{provider_id}/{model_id}",
                        "label": f"{provider_label} · {model_label}",
                        "supported_efforts": supported_efforts,
                        "default_effort": None,
                    }
                )
        flattened.sort(key=lambda entry: entry["id"])
        return flattened

    def _select_default_model(
        self,
        models: list[dict[str, Any]],
        providers: dict[str, Any],
    ) -> tuple[str, str | None]:
        available = {entry["id"] for entry in models}
        default_id: str | None = None
        if DEFAULT_OPENCODE_MODEL in available:
            default_id = DEFAULT_OPENCODE_MODEL
        else:
            defaults = providers.get("default") or {}
            if isinstance(defaults, dict):
                for provider_id, model_id in defaults.items():
                    if isinstance(provider_id, str) and isinstance(model_id, str):
                        candidate = f"{provider_id}/{model_id}"
                        if candidate in available:
                            default_id = candidate
                            break
            if default_id is None and models:
                default_id = models[0]["id"]
        if default_id is None:
            default_id = DEFAULT_OPENCODE_MODEL

        for model in models:
            if model["id"] == default_id:
                return default_id, model["label"]
        return default_id, None

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: Any,
    ) -> SessionRecord | None:
        text = getattr(request, "text", None) or ""
        if not text.startswith("/"):
            return None

        adapter = self._require_adapter(runtime, session.launch_target_id, session.cwd)
        rest = text[1:].split(maxsplit=1)
        command = rest[0] if rest else ""

        if command == "compact":
            try:
                await adapter.compact_session(session.id)
            except OpenCodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            await runtime._record_system_event(
                session.id,
                "Compacting session...",
                status=SessionStatus.RUNNING,
            )
            return runtime.get_session(session.id)

        return None

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        adapter = self._require_adapter(runtime, session.launch_target_id, session.cwd)
        request_id = tool_use_id or adapter.current_question_id(session.id)
        if not request_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no pending question to answer",
            )
        structured_answers = self._serialize_question_answers(answer, answers)
        success = await adapter.answer_question(
            session.id,
            request_id,
            structured_answers,
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="failed to answer question",
            )
        updated = runtime.storage.update_session(
            session.id, status=SessionStatus.RUNNING
        )
        metadata: dict[str, Any] = {"kind": "ask_user_question_answer"}
        if answers:
            metadata["answers"] = answers
        metadata["tool_use_id"] = request_id
        await runtime._record_user_event(
            session.id,
            answer,
            submit=True,
            extra_metadata=metadata,
        )
        return updated

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        pass

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        key = self._adapter_key(runtime, session.launch_target_id, session.cwd)
        try:
            adapter = await self._get_or_create_adapter(
                runtime, session.launch_target_id, session.cwd
            )
        except Exception as exc:
            self._health_for(key).record_failure()
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"OpenCode adapter unavailable; cannot restore: {exc}",
                status=SessionStatus.ERROR,
            )
            return
        opencode_session_id = session.transport_state.get("opencode_session_id")
        if not opencode_session_id:
            runtime.storage.update_session(session.id, status=SessionStatus.EXITED)
            await runtime._record_system_event(
                session.id,
                "OpenCode session has no opencode_session_id; marking exited",
                status=SessionStatus.EXITED,
            )
            return
        try:
            await adapter.restore_session(
                session.id,
                session.cwd,
                opencode_session_id,
                model=session.model,
                agent=session.transport_state.get("agent"),
                effort=session.effort,
            )
            pre_plan_mode = session.transport_state.get("pre_plan_mode")
            if pre_plan_mode is not None:
                await adapter.set_pre_plan_mode(session.id, pre_plan_mode)
        except Exception as exc:
            self._health_for(key).record_failure()
            log.exception("opencode restore failed", extra={"session_id": session.id})
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"OpenCode session restore failed: {exc}",
                status=SessionStatus.ERROR,
            )
            return
        self._health_for(key).record_success()
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await runtime._record_system_event(
            session.id,
            "OpenCode session restored from previous backend process",
            status=SessionStatus.IDLE,
        )

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
    ) -> list[OpenCodeThreadSummary]:
        adapters = self._adapters_for_launch_target(launch_target_id)
        if not adapters:
            adapters = [
                await self._get_or_create_adapter(runtime, launch_target_id, None)
            ]
        imported = {
            (s.transport_state.get("opencode_session_id"), s.launch_target_id)
            for s in runtime.storage.list_sessions()
            if s.backend == self.id
        }
        result = []
        seen: set[str] = set()
        for adapter in adapters:
            # One bad adapter (e.g. an ERROR session restored at boot whose
            # persisted cwd no longer resolves) must not take down thread
            # discovery for the rest. Skip and log.
            try:
                sessions = await adapter.list_sessions()
            except Exception:
                log.exception(
                    "opencode list_sessions failed for adapter; skipping",
                    extra={"workdir": adapter._workdir},
                )
                continue
            for sess in sessions:
                sess_id = sess.get("id")
                if not sess_id or sess_id in seen:
                    continue
                seen.add(sess_id)
                if (sess_id, launch_target_id) in imported:
                    continue
                time_data = sess.get("time", {})
                result.append(
                    OpenCodeThreadSummary(
                        id=sess_id,
                        title=sess.get("title", "Untitled"),
                        directory=sess.get("directory"),
                        created_at=time_data.get("created"),
                        updated_at=time_data.get("updated"),
                    )
                )
        result.sort(
            key=lambda thread: (
                thread.updated_at if thread.updated_at is not None else -1,
                thread.created_at if thread.created_at is not None else -1,
                thread.id,
            ),
            reverse=True,
        )
        return result

    async def import_thread(
        self, runtime: "SessionRuntime", request: OpenCodeThreadImportRequest
    ) -> SessionRecord:
        launch_target_id = getattr(request, "launch_target_id", None)
        requested_cwd = getattr(request, "cwd", None)
        opencode_session_id = request.thread_id
        for s in runtime.storage.list_sessions():
            if (
                s.backend == self.id
                and s.transport_state.get("opencode_session_id") == opencode_session_id
                and s.launch_target_id == launch_target_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="session already imported",
                )

        # Fetch the session first so the adapter we cache and the
        # SessionRecord we persist agree on cwd. The session already exists
        # on the OpenCode server with whatever directory it was created in;
        # keying the adapter by `requested_cwd` here would orphan it from
        # later `_require_adapter(..., session.cwd)` lookups.
        fetch_adapter = self._find_adapter_for_launch_target(launch_target_id)
        if fetch_adapter is None:
            fetch_adapter = await self._get_or_create_adapter(
                runtime, launch_target_id, requested_cwd
            )
        sess = await fetch_adapter.get_session(opencode_session_id)
        if not sess:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="session not found in OpenCode",
            )
        raw_directory = sess.get("directory")
        cwd = (
            raw_directory
            if isinstance(raw_directory, str) and raw_directory
            else (requested_cwd or ".")
        )
        adapter = await self._get_or_create_adapter(runtime, launch_target_id, cwd)
        session_id = runtime._generate_session_id(self.id)
        session_dir = runtime._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        raw_log.touch(exist_ok=True)
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=self.id,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=sess.get("title", "Imported session"),
            cwd=cwd,
            launch_target_id=launch_target_id,
            repo_name=None,
            branch=None,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"opencode_session_id": opencode_session_id},
            permission_mode="ask",
        )
        runtime.storage.create_session(session)
        try:
            await adapter.restore_session(
                session.id,
                cwd,
                opencode_session_id,
            )
        except Exception as exc:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to restore session: {exc}",
            ) from exc
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await runtime._record_system_event(
            session.id,
            f"Imported OpenCode session ({opencode_session_id})",
            status=SessionStatus.IDLE,
        )
        return runtime.get_session(session.id)

    async def create_session(
        self,
        runtime: "SessionRuntime",
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
    ) -> SessionRecord:
        launch_target_id = launch_target.id if launch_target else None
        try:
            adapter = await self._get_or_create_adapter(
                runtime, launch_target_id, request.cwd
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"opencode adapter not configured: {exc}",
            ) from exc
        raw_log.touch(exist_ok=True)
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=request.backend,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=title,
            cwd=request.cwd,
            launch_target_id=launch_target.id if launch_target else None,
            repo_name=git_meta.repo_name,
            branch=git_meta.branch,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={},
            permission_mode=permission_mode,
            model=resolved_model,
            effort=resolved_effort,
        )
        runtime.storage.create_session(session)

        agent = "plan" if permission_mode == "plan" else None
        mapped_permission = _ruleset_for_mode(
            "default" if permission_mode == "plan" else permission_mode
        )

        try:
            opencode_session_id = await adapter.start_session(
                session_id,
                request.cwd,
                model=resolved_model,
                effort=resolved_effort,
                agent=agent,
                title=title,
                permission=mapped_permission,
            )
            session.transport_state = {"opencode_session_id": opencode_session_id}
            runtime.storage.update_session(
                session.id, transport_state=session.transport_state
            )
        except OpenCodeError as exc:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        # The adapter already emits "OpenCode session started ({session_id})"
        # with the remote id; re-recording here would duplicate the note.
        return runtime.get_session(session.id)

    def _serialize_question_answers(
        self,
        answer: str,
        answers: list[dict[str, Any]] | None,
    ) -> list[list[str]]:
        if not answers:
            return [[answer]]

        structured: list[list[str]] = []
        for entry in answers:
            selected: list[str] = []
            answer_value = entry.get("answer")
            if isinstance(answer_value, str) and answer_value.strip():
                selected.extend(
                    item.strip() for item in answer_value.split(",") if item.strip()
                )
            notes = entry.get("notes")
            if isinstance(notes, str) and notes.strip():
                selected.append(notes.strip())
            if selected:
                structured.append(selected)

        return structured or [[answer]]


def build_plugin() -> OpenCodePlugin:
    return OpenCodePlugin()
