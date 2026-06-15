"""Claude Code backend plugin.

Owns the per-backend invariants that the runtime previously hard-coded:
permission-mode catalogue, model catalogue, capability flags, transport
adapter wiring, lifecycle (start/restore/import), control surface
(set_model/effort/permission_mode), thread enumeration, and the
system-note formatters. The runtime delegates by id; backend literals
no longer leak into runtime.py.
"""

import asyncio
import logging
import shlex
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from waypoint.backends.base import DefaultLaunchContract
from waypoint.backends.capabilities import (
    BackendCapabilities,
    ModelSource,
)
from waypoint.backends.claude_code.adapter import ClaudeCliAdapter, ClaudeCliError
from waypoint.backends.claude_code.commands import (
    CLAUDE_BUILTIN_SLASH_COMMANDS,
    list_claude_command_completions,
)
from waypoint.backends.claude_code.models import (
    CLAUDE_EFFORT_LEVELS,
    DEFAULT_CLAUDE_MODELS,
    claude_default_model_id,
)
from waypoint.backends.claude_code.permission_modes import (
    CLAUDE_PERMISSION_MODE_SPECS,
    CLAUDE_PERMISSION_MODES,
    claude_permission_mode_label,
)
from waypoint.backends.claude_code.rate_limits import (
    probe_claude_usage,
    probe_claude_usage_remote,
)
from waypoint.backends.claude_code.remote import build_remote_claude_launch_factory
from waypoint.backends.claude_code.schemas import (
    ClaudeThreadImportRequest,
    ClaudeThreadSummary,
)
from waypoint.backends.claude_code.support import (
    ClaudeSupportBundle,
    ensure_claude_support_bundle,
)
from waypoint.backends.claude_code.threads import (
    ClaudeThreadInfo,
    find_local_claude_thread,
    list_local_claude_threads,
)
from waypoint.backends.claude_code.threads_remote import RemoteClaudeThreadEnumerator
from waypoint.backends.completions import static_slash_completions
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
from waypoint.backends.tmux.plugin import TmuxPlugin
from waypoint.git_meta import GitMeta
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import (
    BackendModelOption,
    CommandCompletion,
    CompletionDispatch,
    EventKind,
    LaunchMode,
    SessionCreateRequest,
    SessionEnvelope,
    SessionInputRequest,
    SessionRateLimitUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


log = logging.getLogger("waypoint.backends.claude_code")

_CLAUDE_ORG_PREFIX = "org: "
_CLAUDE_ORG_TIER_PREFIX = "org tier: "


def _find_prefixed(notes: list[str], prefix: str) -> str | None:
    for note in notes:
        if note.startswith(prefix):
            value = note[len(prefix) :].strip()
            if value:
                return value
    return None


class ClaudeCodePluginConfig(PluginConfig):
    """Claude Code plugin configuration block.

    Owns the curated model catalogue (no live ``model/list`` RPC for
    Claude — the binary's per-model factory map is mirrored statically
    here).
    """

    models: list[BackendModelOption] = Field(
        default_factory=lambda: list(DEFAULT_CLAUDE_MODELS)
    )
    default_model_id: str | None = Field(default_factory=claude_default_model_id)
    # Deprecated no-op: tool approval moved from the PreToolUse HTTP hook to
    # the `can_use_tool` control protocol, which has no network timeout.
    # Retained so existing configs that still set it keep loading.
    hook_timeout_seconds: int = Field(default=3600, ge=1)


class ClaudeCodeLaunchTargetConfig(PluginLaunchTargetConfig):
    """Per-target overrides for Claude Code on an SSH launch target."""

    # Deprecated no-op; see ``ClaudeCodePluginConfig.hook_timeout_seconds``.
    hook_timeout_seconds: int | None = Field(default=None, ge=1)


class ClaudeCodePlugin(DefaultLaunchContract):
    id = "claude_code"
    transport_id = "claude_cli"
    # Defaults to the tty-tail driver (the faithful TUI), with the native
    # stream-json adapter and the generic tmux pane wrapper also available; the
    # wrapper doubles as the managed-launch fallback.
    supported_transports = ("claude_cli", "claude_tty", "tmux")
    default_transport = "claude_tty"
    label = "Claude Code"
    import_request_schema: type[BaseModel] | None = ClaudeThreadImportRequest
    config_schema: type[PluginConfig] = ClaudeCodePluginConfig
    launch_target_schema: type[PluginLaunchTargetConfig] = ClaudeCodeLaunchTargetConfig
    # Force the fullscreen Ink renderer. Claude's startup capability
    # probe (DA1 / XTVERSION / DECRQM 2026) races SSH latency on remote
    # tmux launches and falls back to an inline mode with no alt-screen
    # and no mouse-tracking. The flag is safe locally too: the fullscreen
    # renderer is what claude picks when detection succeeds.
    extra_env = {"CLAUDE_CODE_NO_FLICKER": "1"}
    capabilities = BackendCapabilities(
        is_structured=True,
        supports_resume=False,
        supports_reattach_after_exit=True,
        supports_set_model_inline=True,
        supports_set_effort_inline=False,
        supports_set_effort_with_restart=True,
        supports_set_permission_mode_inline=True,
        supports_thread_discovery=True,
        supports_thread_import=True,
        supports_fork=True,
        supports_slash_compact=False,
        supports_approval_note=True,
        supports_attachments=True,
        supports_custom_cli_args=True,
        # One-shot approve/decline only. The binary ignores the can_use_tool
        # response's permission_updates in -p mode (verified against v2.1.157:
        # addRules/setMode neither suppress re-prompts nor persist to settings),
        # so "approve for session"/"always allow" would be dead buttons. Re-add
        # once in-session suppression is implemented adapter-side.
        approval_decisions=("approve", "decline"),
        permission_modes=CLAUDE_PERMISSION_MODE_SPECS,
        effort_levels=CLAUDE_EFFORT_LEVELS,
        model_source=ModelSource.STATIC,
        slash_commands=CLAUDE_BUILTIN_SLASH_COMMANDS,
        badges={"glyph": "C", "color": "#a78bfa"},
        cli_binary="claude",
        target_aliases=("claude",),
    )

    def __init__(self) -> None:
        self.adapter: ClaudeCliAdapter | None = None
        self.support: ClaudeSupportBundle | None = None
        self.thread_enumerator: RemoteClaudeThreadEnumerator | None = None

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        # Imported lazily to avoid the cycle: transport → adapter →
        # permission_modes → backends/claude_code/__init__ → plugin.
        from waypoint.backends.claude_code.transport import ClaudeTransport

        return ClaudeTransport(runtime, self)

    def setup(self, runtime: "SessionRuntime") -> None:
        # Build the host-side support bundle, the CLI adapter, and the remote
        # thread enumerator — collectively the "claude side" of the runtime.
        # Resilient to ensure_claude_support_bundle failing (read-only data
        # dir, missing scripts, etc.); we log and leave self.adapter=None so
        # the runtime keeps working without Claude support and the tmux
        # fallback path takes over.
        try:
            support = ensure_claude_support_bundle(runtime.settings.data_dir)
        except Exception:  # noqa: BLE001
            log.exception("claude support bundle setup failed; claude support disabled")
            self.support = None
            self.adapter = None
            self.thread_enumerator = None
            return
        self.support = support
        self.adapter = ClaudeCliAdapter(
            runtime._emit_adapter_event,
            on_init=runtime.handle_completion_source_init,
            on_session_update=runtime.session_update_callback(),
            default_model_id=self._config(runtime).default_model_id,
        )
        self.thread_enumerator = RemoteClaudeThreadEnumerator(
            support.thread_enumerator_path
        )

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        if self.adapter is not None:
            await self.adapter.shutdown()
            self.adapter = None
        self.support = None
        self.thread_enumerator = None

    def _require_adapter(self) -> ClaudeCliAdapter:
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="claude adapter is not initialized",
            )
        return self.adapter

    async def _register_local_rate_limit_probe(
        self, runtime: "SessionRuntime", session_id: str
    ) -> None:
        if self.adapter is None:
            return

        async def _probe() -> SessionRateLimitUsage | None:
            return await probe_claude_usage()

        await self.adapter.register_rate_limit_probe(
            session_id, _probe, refresh_interval_seconds=300.0
        )

    async def _register_remote_rate_limit_probe(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        launch_target: SshLaunchTargetConfig,
    ) -> None:
        if self.adapter is None:
            return

        async def _probe() -> SessionRateLimitUsage | None:
            return await probe_claude_usage_remote(launch_target)

        await self.adapter.register_rate_limit_probe(
            session_id, _probe, refresh_interval_seconds=300.0
        )

    async def _register_rate_limit_probe(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> None:
        if launch_target is None:
            await self._register_local_rate_limit_probe(runtime, session_id)
            return
        await self._register_remote_rate_limit_probe(runtime, session_id, launch_target)

    async def refresh_rate_limit_usage(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        launch_target = (
            runtime._find_launch_target(session.launch_target_id)
            if session.launch_target_id
            else None
        )
        await self._register_rate_limit_probe(runtime, session.id, launch_target)
        # Run the probe inline so the caller's HTTP response carries the
        # post-refresh snapshot — otherwise the response races the WS push
        # from the periodic loop and the UI sees stale data.
        if self.adapter is not None:
            await self.adapter.force_refresh_rate_limit_usage(session.id)

    async def probe_account_rate_limit(
        self,
        runtime: "SessionRuntime",
        launch_target: SshLaunchTargetConfig | None,
        *,
        cwd: str | None = None,
    ) -> SessionRateLimitUsage | None:
        """Fetch the account's current rate-limit snapshot without a session.

        The upstream probe (HTTP call to api.anthropic.com via cached OAuth
        creds) is account-scoped, not session-scoped — same call the
        per-session adapter probe makes. Exposed so the tmux fallback can
        populate ``rate_limit_usage`` for wrapped-claude sessions without
        wiring them through the structured adapter. ``cwd`` is accepted for
        a uniform probe signature across agents but unused — Claude's probe
        is independent of the working directory.
        """
        _ = cwd
        if launch_target is None:
            return await probe_claude_usage()
        return await probe_claude_usage_remote(launch_target)

    def rate_limit_account(
        self, snapshot: SessionRateLimitUsage
    ) -> tuple[str, str] | None:
        """Derive the usage-dashboard ``(account_key, account_label)``.

        Claude rate limits are scoped to an org; the snapshot's ``notes``
        carry ``org: <name>`` and ``org tier: <tier>``. Returns ``None``
        when no org note is present so the dashboard falls back to a
        session-scoped bucket.
        """
        org = _find_prefixed(snapshot.notes, _CLAUDE_ORG_PREFIX)
        if org is None:
            return None
        tier = _find_prefixed(snapshot.notes, _CLAUDE_ORG_TIER_PREFIX)
        label = f"{org} · {tier}" if tier else org
        return f"{self.id}:{org}", label

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        # The Claude adapter is wired up lazily by setup() — if the support
        # bundle failed to materialise we leave self.adapter=None and the
        # runtime falls through to the tmux plugin so the user still gets a
        # session.
        return self.adapter is not None

    def remote_executable(self, launch_target: SshLaunchTargetConfig) -> str:
        return launch_target.remote_bin_for(self.id, self.capabilities.cli_binary) or ""

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        if self.adapter is not None:
            await self.adapter.terminate_session(session.id)

    def native_thread_id(self, session: SessionRecord) -> str | None:
        thread_id = session.transport_state.get("thread_id")
        return thread_id if isinstance(thread_id, str) else None

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Claude caches per-launch-target thread listings remotely;
        # invalidate so a re-import after delete sees the freed slot.
        if session.launch_target_id and self.thread_enumerator is not None:
            self.thread_enumerator.invalidate(session.launch_target_id)
        # Drop any todo tracker stashed for a respawn that will never come.
        if self.adapter is not None:
            self.adapter.discard_session(session.id)

    def register_routes(self, app: FastAPI, context: Any) -> None:
        # Tool approval now rides the `can_use_tool` control protocol over the
        # CLI's stdio stream (see adapter._handle_can_use_tool), so the backend
        # no longer mounts a PreToolUse approval webhook.
        return

    def validate_permission_mode(self, mode: str | None) -> str | None:
        if mode is None or mode == "":
            return None
        if mode not in CLAUDE_PERMISSION_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"unsupported {self.id} permission mode: {mode}; "
                    f"expected one of {', '.join(CLAUDE_PERMISSION_MODES)}"
                ),
            )
        return mode

    def _config(self, runtime: "SessionRuntime") -> ClaudeCodePluginConfig:
        config = runtime.settings.plugin_config(self.id)
        assert isinstance(config, ClaudeCodePluginConfig)
        return config

    def _effective_args(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None,
        custom_args: list[str],
    ) -> list[str]:
        if launch_target_id:
            launch_target = runtime._find_launch_target(launch_target_id)
            if launch_target:
                target_config = launch_target.plugin_config(self.id)
                if target_config:
                    return target_config.cli_args + custom_args
            return list(custom_args)
        return self._config(runtime).cli_args + custom_args

    def static_model_options(self, runtime: "SessionRuntime") -> list[Any]:
        # Plugin config carries the (configurable) Claude model catalogue.
        # Deployments patch the list via ``plugin_configs.claude_code.models``
        # in waypoint.yaml without forking this module.
        return list(self._config(runtime).models)

    @property
    def permission_mode_ids(self) -> tuple[str, ...]:
        return CLAUDE_PERMISSION_MODES

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        before_pending = self.adapter.pending_approval_ids(session.id)
        try:
            await self.adapter.set_permission_mode(session.id, mode)
        except Exception as exc:  # noqa: BLE001 — surface adapter errors as 400
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        after_pending = set(self.adapter.pending_approval_ids(session.id))
        cleared_pending = [
            approval_id
            for approval_id in before_pending
            if approval_id not in after_pending
        ]
        if not cleared_pending:
            return
        next_status = (
            SessionStatus.WAITING_INPUT if after_pending else SessionStatus.RUNNING
        )
        runtime.storage.update_session(session.id, status=next_status)
        mode_label = claude_permission_mode_label(mode)
        for approval_id in cleared_pending:
            await runtime._record_system_event(
                session.id,
                f"Pending approval cleared by permission mode change to {mode_label}",
                status=next_status,
                metadata={
                    "method": "approval.invalidated",
                    "approval_id": approval_id,
                    "permission_mode": mode,
                },
            )

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        try:
            await self.adapter.set_model(session.id, model)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    async def apply_effort(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        effort: str | None,
    ) -> bool:
        """Returns True when the runtime should also publish a system
        note describing the effort swap; False signals "nothing changed".
        """
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        # Claude has no in-process effort knob — set_effort terminates the
        # CLI and respawns it with `--resume <id> --effort <new>`. Skip
        # the swap when the value is unchanged so we don't restart for
        # nothing.
        if effort == (session.effort or None):
            return False
        try:
            await self.adapter.set_effort(session.id, effort)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        return True

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
    ) -> dict[str, Any]:
        config = self._config(runtime)
        default_model = config.default_model_id
        default_effort = config.default_effort
        options = [opt.model_dump(mode="json") for opt in config.models]
        default_model_id: str | None = None
        if default_model is None:
            for opt in config.models:
                if opt.is_default:
                    default_model_id = opt.id
                    break
        else:
            default_model_id = default_model
        default_model_label: str | None = None
        if default_model_id:
            for opt in config.models:
                if opt.id == default_model_id:
                    default_model_label = opt.label
                    break
        return {
            "backend": self.id,
            "models": options,
            "default_model_id": default_model_id,
            "default_model_label": default_model_label,
            "default_effort": default_effort,
            "supports_free_text": True,
        }

    async def list_command_completions(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        *,
        trigger: str = "/",
        prefix: str = "",
        force_refresh: bool = False,
    ) -> list[CommandCompletion]:
        if trigger != "/":
            return []
        runtime_commands = _session_slash_commands(
            self.adapter, session.id
        ) or _session_transport_slash_commands(session)
        completions = (
            []
            if _commands_include_name(runtime_commands, "status")
            else _claude_waypoint_completions(prefix)
        )
        completions.extend(_claude_runtime_slash_completions(runtime_commands, prefix))
        # Curated built-ins as a baseline for sessions with no live slash-command
        # stream (tmux transport), deduped against anything the runtime reported.
        present = {f"{item.trigger}{item.name}" for item in completions}
        for builtin in static_slash_completions(
            self.id, self.capabilities, prefix=prefix
        ):
            key = f"{builtin.trigger}{builtin.name}"
            if key not in present:
                completions.append(builtin)
                present.add(key)
        launch_target = (
            runtime._find_launch_target(session.launch_target_id)
            if session.launch_target_id
            else None
        )
        claude_bin = (
            self.remote_executable(launch_target)
            if launch_target is not None
            else self._config(runtime).local_bin or self.capabilities.cli_binary
        )
        if not claude_bin:
            return completions
        try:
            dynamic = await list_claude_command_completions(
                cwd=session.cwd,
                claude_bin=claude_bin,
                prefix=prefix,
                launch_target=launch_target,
            )
        except Exception:
            return completions
        seen = {f"{item.trigger}{item.name}" for item in completions}
        for item in dynamic:
            key = f"{item.trigger}{item.name}"
            if key in seen:
                _merge_completion_metadata(completions, key, item)
                continue
            completions.append(item)
            seen.add(key)
        return completions

    def effort_swap_message(self, effort: str | None) -> str:
        return _claude_effort_swap_message(effort)

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: SessionInputRequest,
    ) -> SessionRecord | None:
        command = _first_slash_command(request.text)
        runtime_commands = _session_slash_commands(
            self.adapter, session.id
        ) or _session_transport_slash_commands(session)
        if command == "/status" and not _commands_include_name(
            runtime_commands, "status"
        ):
            await runtime._record_user_event(
                session.id,
                request.text,
                submit=request.submit,
                status=session.status,
            )
            await runtime._record_system_event(
                session.id,
                _format_claude_status(session, self.adapter),
                status=session.status,
                metadata={"builtin_command": "/status", "source": "waypoint"},
            )
            return runtime.get_session(session.id)
        if command == "/copy":
            # Claude's native /copy emits OSC 52 from the interactive TUI;
            # the SDK's --print --output-format=stream-json mode never
            # surfaces that escape, so the slash command is a silent no-op
            # for structured sessions unless we intercept here. Tmux-
            # wrapped sessions take a different maybe_handle_input path
            # (tmux/plugin.py) where the CLI's OSC 52 reaches xterm
            # directly, so this branch is structured-only by construction.
            text = _last_assistant_text(runtime, session.id)
            await runtime._record_user_event(
                session.id,
                request.text,
                submit=request.submit,
                status=session.status,
            )
            if text:
                await runtime.broadcast.publish(
                    SessionEnvelope(
                        type="clipboard_copy",
                        payload={"text": text},
                    ),
                    session_id=session.id,
                )
                note = (
                    f"Copied last response to clipboard "
                    f"({len(text)} chars, {text.count(chr(10)) + 1} lines)"
                )
            else:
                note = "Nothing to copy — no assistant response yet."
            await runtime._record_system_event(
                session.id,
                note,
                status=session.status,
                metadata={"builtin_command": "/copy", "source": "waypoint"},
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
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="answer-question is only supported for Claude sessions",
            )
        try:
            handled = await self.adapter.respond_to_ask_question(
                session.id, answer, tool_use_id
            )
        except ClaudeCliError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        if not handled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no pending question for this session",
            )
        # Stash structured per-question answers + notes so the frontend
        # renders this user_input as a styled "answers" card instead of
        # the raw `"<question>"="<answer>" user notes: …` payload Claude
        # was tuned around.
        extra: dict[str, Any] = {"kind": "ask_user_question_answer"}
        if answers:
            extra["answers"] = answers
        if tool_use_id:
            extra["tool_use_id"] = tool_use_id
        # Same ordering as handle_input: flip status to RUNNING before
        # _record_user_event broadcasts the session_state snapshot,
        # otherwise the spinner stays off until Claude's next chunk.
        updated = runtime.storage.update_session(
            session.id, status=SessionStatus.RUNNING
        )
        await runtime._record_user_event(
            session.id, answer, submit=True, extra_metadata=extra
        )
        return updated

    async def approve_plan(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        plan_item_id: str,
        decision: str,
        text: str | None,
    ) -> SessionRecord:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"plan approval is not supported for {self.id}",
        )

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        # Side-effect of an ExitPlanMode approval: the Claude adapter
        # has already flipped the binary's permission mode to default
        # via set_permission_mode. Sync storage + broadcast so the UI
        # pill reflects the change instead of staying stuck on "plan".
        if self.adapter is None:
            return
        current = self.adapter.session_permission_mode(session.id)
        if current is None:
            return
        previous = session.permission_mode or "default"
        if current == previous:
            return
        runtime.storage.update_session(session.id, permission_mode=current)
        await runtime.broadcast.publish(
            SessionEnvelope(
                type="session_list_update",
                payload={
                    "sessions": [
                        item.model_dump(mode="json") for item in runtime.list_sessions()
                    ]
                },
            )
        )

    async def fork_session(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        new_session_id: str,
        title: str,
        raw_log: Path,
        structured_log: Path,
    ) -> SessionRecord:
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="claude adapter is not initialized",
            )
        launch_target = (
            runtime._find_launch_target(session.launch_target_id)
            if session.launch_target_id
            else None
        )
        thread_id = session.transport_state.get("thread_id")
        if not thread_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude session has no thread id to fork from",
            )
        if (
            session.launch_target_id
            and runtime._find_launch_target(session.launch_target_id) is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"claude session launch target {session.launch_target_id} is no longer configured",
            )

        new_claude_session_id = self.generate_session_id()
        try:
            await self.adapter.start_session(
                new_session_id,
                session.cwd,
                new_claude_session_id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
                custom_args=self._effective_args(
                    runtime, session.launch_target_id, session.args
                ),
                fork_from_claude_session_id=thread_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "claude fork failed",
                extra={
                    "session_id": session.id,
                    "claude_session_id": thread_id,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

        now = datetime.now(UTC)
        raw_log.touch(exist_ok=True)
        new_session = SessionRecord(
            id=new_session_id,
            backend=self.id,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=title,
            cwd=session.cwd,
            launch_target_id=session.launch_target_id,
            repo_name=session.repo_name,
            branch=session.branch,
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": new_claude_session_id},
            permission_mode=session.permission_mode,
            model=session.model,
            effort=session.effort,
            args=session.args,
            config_overrides=session.config_overrides,
        )
        runtime.storage.create_session(new_session)
        runtime.storage.clone_events(session.id, new_session_id)
        await self._register_rate_limit_probe(runtime, new_session_id, launch_target)
        await runtime._record_system_event(
            new_session_id,
            self.format_restore_message(runtime, session.cwd, session.launch_target_id)
            + f" (forked from {session.title or session.id})",
            status=SessionStatus.IDLE,
        )
        return runtime.get_session(new_session_id)

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        if not _session_transport_slash_commands(session):
            slash_commands = _latest_stored_slash_commands(runtime, session.id)
            if slash_commands:
                state = dict(session.transport_state)
                state["slash_commands"] = list(slash_commands)
                session = runtime.storage.update_session(
                    session.id, transport_state=state
                )
        if self.adapter is None:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                "Claude adapter unavailable; cannot restore",
                status=SessionStatus.ERROR,
            )
            return
        thread_id = session.transport_state.get("thread_id")
        if not thread_id:
            runtime.storage.update_session(session.id, status=SessionStatus.EXITED)
            await runtime._record_system_event(
                session.id,
                "Claude session has no claude_session_id; marking exited",
                status=SessionStatus.EXITED,
            )
            return
        if (
            session.launch_target_id
            and runtime._find_launch_target(session.launch_target_id) is None
        ):
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Claude session launch target {session.launch_target_id} is no longer configured",
                status=SessionStatus.ERROR,
            )
            return
        try:
            await self.adapter.restore_session(
                session.id,
                session.cwd,
                thread_id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
                custom_args=self._effective_args(
                    runtime, session.launch_target_id, session.args
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "claude restore failed",
                extra={
                    "session_id": session.id,
                    "claude_session_id": thread_id,
                },
            )
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Claude session restore failed: {exc}",
                status=SessionStatus.ERROR,
            )
            return
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await self._register_rate_limit_probe(
            runtime,
            session.id,
            runtime._find_launch_target(session.launch_target_id),
        )
        await runtime._record_system_event(
            session.id,
            self.format_restore_message(runtime, session.cwd, session.launch_target_id),
            status=SessionStatus.IDLE,
        )

    def format_start_message(
        self,
        claude_session_id: str,
        cwd: str | None,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str:
        if launch_target is not None:
            return (
                f"Claude session started via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd}) ({claude_session_id})"
            )
        return f"Claude session started ({claude_session_id})"

    def format_restore_message(
        self,
        runtime: "SessionRuntime",
        cwd: str | None,
        launch_target_id: str | None,
    ) -> str:
        launch_target = runtime._find_launch_target(launch_target_id)
        if launch_target is not None:
            return (
                f"Claude session restored via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd or launch_target.default_cwd})"
            )
        return "Claude session restored from previous backend process"

    def format_import_message(
        self,
        cwd: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> str:
        if launch_target is not None:
            return (
                f"Imported stored Claude thread via SSH target {launch_target.name} "
                f"on {launch_target.ssh_destination} ({cwd})"
            )
        return f"Imported stored Claude thread ({cwd})"

    # --- agent launch contract ---------------------------------------
    #
    # Pane-wrapper launch knowledge a generic transport drives without
    # knowing it's Claude. ``capture_thread_id`` stays the inert
    # ``DefaultLaunchContract`` no-op: Claude pregenerates its id via
    # ``--session-id``, so there's nothing to discover post-launch.

    def launch_flags(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
    ) -> list[str]:
        flags: list[str] = []
        if model:
            flags += ["--model", model]
        if effort:
            flags += ["--effort", effort]
        if permission_mode:
            flags += ["--permission-mode", permission_mode]
        return flags

    def pregenerate_thread_id(self) -> str | None:
        return str(uuid.uuid4())

    def resume_args(self, thread_id: str, prior_args: list[str]) -> list[str]:
        # prior_args may carry ``--session-id <uuid>`` (the initial create
        # form) or ``--resume <uuid>`` (a prior reconnect's output). Strip
        # both so the new prefix doesn't compound on repeated reconnects.
        scrubbed: list[str] = []
        skip = 0
        for arg in prior_args:
            if skip:
                skip -= 1
                continue
            if arg in ("--session-id", "--resume"):
                skip = 1
                continue
            scrubbed.append(arg)
        return ["--resume", thread_id, *scrubbed]

    async def conversation_exists(
        self,
        thread_id: str,
        cwd: str,
        launch_target: SshLaunchTargetConfig | None,
    ) -> bool:
        # ~/.claude/projects/<dashed-absolute-cwd>/<uuid>.jsonl — but the
        # dashed key uses claude's view of the absolute cwd, which may not
        # match what we have on hand (SSH sessions carry the raw ``~/foo``
        # form; ``cd`` symlinks resolve on the remote). UUIDs are globally
        # unique under projects/, so glob across all project dirs and pick
        # the file by name.
        needle = f"{thread_id}.jsonl"
        if launch_target is None:
            projects = Path.home() / ".claude" / "projects"
            if not projects.is_dir():
                return False
            return any(projects.glob(f"*/{needle}"))
        # ``$HOME`` must be left *outside* the quoted needle so the remote
        # shell expands it; shlex-quoting the whole path would single-quote
        # the dollar and look for a literal ``$HOME`` directory.
        stdout = await launch_target.ssh_capture(
            f"ls $HOME/.claude/projects/*/{shlex.quote(needle)} "
            "2>/dev/null | head -n 1",
        )
        return bool(stdout.strip())

    # --- launch / discovery helpers ----------------------------------

    def launch_factory(self, runtime: "SessionRuntime", launch_target_id: str | None):
        launch_target = runtime._find_launch_target(launch_target_id)
        if launch_target is None or self.adapter is None:
            return None
        return build_remote_claude_launch_factory(launch_target)

    def generate_session_id(self) -> str:
        return str(uuid.uuid4())

    def _thread_summary(self, info: ClaudeThreadInfo) -> ClaudeThreadSummary:
        return ClaudeThreadSummary(
            id=info.id,
            title=info.title,
            cwd=info.cwd,
            repo_name=info.repo_name,
            branch=info.branch,
            preview=info.preview,
            created_at=info.created_at,
            updated_at=info.updated_at,
        )

    def _find_imported_session(
        self,
        runtime: "SessionRuntime",
        thread_id: str,
        launch_target_id: str | None,
    ) -> SessionRecord | None:
        for session in runtime.storage.list_sessions():
            if session.backend != self.id:
                continue
            if session.transport_state.get("thread_id") != thread_id:
                continue
            if session.launch_target_id != launch_target_id:
                continue
            return session
        return None

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
    ) -> list[ClaudeThreadSummary]:
        if self.adapter is None:
            return []
        imported: set[tuple[str | None, str]] = set()
        for session in runtime.storage.list_sessions():
            if session.backend != self.id:
                continue
            thread_id = session.transport_state.get("thread_id")
            if not thread_id:
                continue
            imported.add((session.launch_target_id, thread_id))
        if launch_target_id is None:
            infos = await asyncio.to_thread(list_local_claude_threads)
        else:
            target = runtime._resolve_launch_target(launch_target_id, self.id)
            if target is None or self.thread_enumerator is None:
                return []
            infos = await self.thread_enumerator.list(target)
        return [
            self._thread_summary(info)
            for info in infos
            if (launch_target_id, info.id) not in imported
        ]

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
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude adapter is not configured on this backend",
            )
        raw_log.touch(exist_ok=True)
        claude_session_id = self.generate_session_id()
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=request.backend,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=title,
            cwd=request.cwd,
            launch_target_id=launch_target.id if launch_target else None,
            launch_mode=request.launch_mode,
            repo_name=git_meta.repo_name,
            branch=git_meta.branch,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": claude_session_id},
            spawner_session_id=request.spawner_session_id,
            permission_mode=permission_mode,
            model=resolved_model,
            effort=resolved_effort,
            args=request.args,
        )
        runtime.storage.create_session(session)
        try:
            await self.adapter.start_session(
                session_id,
                request.cwd,
                claude_session_id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
                custom_args=self._effective_args(
                    runtime, session.launch_target_id, session.args
                ),
            )
        except (ClaudeCliError, FileNotFoundError, OSError) as exc:
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await self._register_rate_limit_probe(runtime, session.id, launch_target)
        await runtime._record_system_event(
            session.id,
            self.format_start_message(claude_session_id, request.cwd, launch_target),
            status=SessionStatus.IDLE,
        )
        return runtime.get_session(session.id)

    async def import_thread(
        self,
        runtime: "SessionRuntime",
        request: ClaudeThreadImportRequest,
        *,
        agent: str | None = None,
    ) -> SessionRecord:
        backend = agent or self.id
        launch_target = runtime._resolve_launch_target(
            request.launch_target_id, self.id
        )
        existing = self._find_imported_session(
            runtime, request.thread_id, request.launch_target_id
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="claude thread already imported",
            )
        # Resolve thread metadata first; cwd is needed for both the
        # direct (Claude SDK) and tmux_wrapper paths.
        if launch_target is None:
            info = await asyncio.to_thread(find_local_claude_thread, request.thread_id)
        else:
            if self.thread_enumerator is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="claude thread enumerator is not initialized",
                )
            info = await self.thread_enumerator.find(launch_target, request.thread_id)
        if info is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="claude thread not found",
            )
        if launch_target is None:
            cwd_path = Path(info.cwd).expanduser()
            if not cwd_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"claude thread cwd {info.cwd} no longer exists; cannot resume"
                    ),
                )
            cwd = str(cwd_path)
        else:
            # Remote cwd lives on the SSH host; we can't stat it from here.
            cwd = info.cwd
        # A pinned transport supersedes launch_mode (mirrors create_session):
        # the agent's native transport takes the structured path below, any
        # other resolves to the tmux wrapper here (the tty-tail driver is
        # dispatched by the runtime before reaching this plugin). With no
        # pinned transport, launch_mode decides: TMUX_WRAPPER always delegates;
        # AUTO falls through when the structured plugin isn't available.
        if request.transport is not None:
            use_resume_wrapper = request.transport != self.transport_id
        else:
            use_resume_wrapper = request.launch_mode == LaunchMode.TMUX_WRAPPER or (
                request.launch_mode == LaunchMode.AUTO
                and not self.is_available_for_managed_launch(runtime)
            )
        if use_resume_wrapper:
            fallback = runtime.registry.fallback_for_managed_launch()
            if not isinstance(fallback, TmuxPlugin):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="tmux fallback launch is not available",
                )
            return await fallback.import_thread_via_resume(
                runtime,
                backend=backend,
                thread_id=request.thread_id,
                cwd=cwd,
                launch_target_id=request.launch_target_id,
                title=info.title,
            )
        # Direct (structured-SDK) path requires the adapter.
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="claude adapter is not initialized",
            )
        session_id = runtime._generate_session_id(self.id)
        session_dir = runtime._session_dir(session_id)
        raw_log = session_dir / "raw.log"
        structured_log = session_dir / "events.jsonl"
        raw_log.touch(exist_ok=True)
        now = datetime.now(UTC)
        session = SessionRecord(
            id=session_id,
            backend=backend,
            source=SessionSource.MANAGED,
            transport=self.transport_id,
            title=info.title,
            cwd=cwd,
            launch_target_id=launch_target.id if launch_target else None,
            repo_name=info.repo_name,
            branch=info.branch,
            status=SessionStatus.STARTING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(raw_log),
            structured_log_path=str(structured_log),
            transport_state={"thread_id": info.id},
            permission_mode="default",
        )
        runtime.storage.create_session(session)
        try:
            await self.adapter.restore_session(
                session.id,
                cwd,
                info.id,
                self.launch_factory(runtime, session.launch_target_id),
                permission_mode=session.permission_mode,
                model=session.model,
                effort=session.effort,
            )
        except (ClaudeCliError, FileNotFoundError, OSError) as exc:
            log.exception(
                "claude import failed",
                extra={
                    "session_id": session.id,
                    "claude_session_id": info.id,
                    "launch_target_id": session.launch_target_id,
                },
            )
            runtime.storage.update_session(session.id, status=SessionStatus.ERROR)
            await runtime._record_system_event(
                session.id,
                f"Claude thread import failed: {exc}",
                status=SessionStatus.ERROR,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"failed to import claude thread: {exc}",
            ) from exc
        if launch_target is not None and self.thread_enumerator is not None:
            self.thread_enumerator.invalidate(launch_target.id)
        runtime.storage.update_session(session.id, status=SessionStatus.IDLE)
        await self._register_rate_limit_probe(runtime, session.id, launch_target)
        await runtime._record_system_event(
            session.id,
            self.format_import_message(cwd, launch_target),
            status=SessionStatus.IDLE,
            metadata={"imported_thread_id": info.id},
        )
        return runtime.get_session(session.id)


def _claude_effort_swap_message(effort: str | None) -> str:
    if effort:
        return f"Restarted Claude session with --effort {effort}"
    return "Restarted Claude session with default effort"


def _claude_waypoint_completions(prefix: str) -> list[CommandCompletion]:
    command = "/status"
    if not _slash_prefix_matches(command, prefix):
        return []
    return [
        CommandCompletion(
            id="claude_code:waypoint:status",
            trigger="/",
            replacement="/status ",
            name="status",
            description="Show Waypoint session status",
            kind="command",
            source="waypoint",
            dispatch=CompletionDispatch.PLAIN_TEXT,
            metadata={"builtin_command": "/status"},
        )
    ]


def _claude_runtime_slash_completions(
    commands: tuple[str, ...], prefix: str
) -> list[CommandCompletion]:
    completions: list[CommandCompletion] = []
    seen: set[str] = set()
    for raw in commands:
        name = _slash_command_name(raw)
        if not name:
            continue
        command = f"/{name}"
        if command in seen or not _slash_prefix_matches(command, prefix):
            continue
        is_plugin_command = ":" in raw
        completions.append(
            CommandCompletion(
                id=f"claude_code:runtime:{raw}",
                trigger="/",
                replacement=f"{command} ",
                name=name,
                description=None,
                kind="skill" if is_plugin_command else "command",
                source="plugin_skill" if is_plugin_command else "claude_builtin",
                dispatch=CompletionDispatch.PLAIN_TEXT,
                metadata={"runtime_command": raw},
            )
        )
        seen.add(command)
    return completions


def _merge_completion_metadata(
    completions: list[CommandCompletion], key: str, incoming: CommandCompletion
) -> None:
    for existing in completions:
        if f"{existing.trigger}{existing.name}" != key:
            continue
        if not existing.description and incoming.description:
            existing.description = incoming.description
        if incoming.metadata:
            existing.metadata = {**existing.metadata, **incoming.metadata}
        if existing.source == "plugin_skill" and incoming.source == "plugin_skill":
            existing.kind = incoming.kind
        return


def _slash_prefix_matches(command: str, prefix: str) -> bool:
    if not prefix:
        return True
    normalized = prefix if prefix.startswith("/") else f"/{prefix}"
    return normalized == "/" or command.startswith(normalized)


def _slash_command_name(raw: str) -> str:
    candidate = raw.rsplit(":", 1)[-1].strip()
    if candidate.startswith("/"):
        candidate = candidate[1:]
    return candidate


def _first_slash_command(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    return stripped.split(maxsplit=1)[0].lower()


def _last_assistant_text(runtime: "SessionRuntime", session_id: str) -> str:
    """Concatenate the assistant's most recent bubble into a single string.

    ``list_events_by_message_count(message_limit=1)`` walks events
    backward and returns events for exactly one logical anchor, so a
    long-running transcript doesn't get fully loaded just to read the
    last bubble. Streaming chunks within the bubble share ``item_id`` →
    same anchor → all chunks are in the slice and join in order.
    Returns empty when the latest anchor isn't an assistant message.
    """
    events = runtime.storage.list_events_by_message_count(session_id, message_limit=1)
    return "".join(
        event.text
        for event in events
        if event.kind == EventKind.AGENT_OUTPUT and event.text
    )


def _session_slash_commands(
    adapter: ClaudeCliAdapter | None, session_id: str
) -> tuple[str, ...]:
    if adapter is None:
        return ()
    getter = getattr(adapter, "session_slash_commands", None)
    if not callable(getter):
        return ()
    commands = getter(session_id)
    return commands if isinstance(commands, tuple) else tuple(commands or ())


def _session_transport_slash_commands(session: SessionRecord) -> tuple[str, ...]:
    commands = session.transport_state.get("slash_commands")
    if not isinstance(commands, list):
        return ()
    return tuple(command for command in commands if isinstance(command, str))


def _latest_stored_slash_commands(
    runtime: "SessionRuntime", session_id: str
) -> tuple[str, ...]:
    for event in reversed(runtime.storage.list_events(session_id)):
        if event.metadata.get("method") != "system.init":
            continue
        payload = event.metadata.get("payload")
        if not isinstance(payload, dict):
            continue
        commands = payload.get("slash_commands")
        if isinstance(commands, list):
            return tuple(command for command in commands if isinstance(command, str))
    return ()


def _commands_include_name(commands: tuple[str, ...], name: str) -> bool:
    return any(_slash_command_name(command) == name for command in commands)


def _format_claude_status(
    session: SessionRecord, adapter: ClaudeCliAdapter | None
) -> str:
    lines = [
        "Claude Code session status",
        f"- Status: {session.status.value}",
        f"- Backend: {session.backend}",
        f"- Transport: {session.transport}",
        f"- CWD: {session.cwd}",
    ]
    if session.launch_target_id:
        lines.append(f"- Launch target: {session.launch_target_id}")
    if session.repo_name:
        repo = session.repo_name
        if session.branch:
            repo = f"{repo} ({session.branch})"
        lines.append(f"- Repo: {repo}")
    if session.model:
        lines.append(f"- Model: {session.model}")
    if session.effort:
        lines.append(f"- Effort: {session.effort}")
    if session.permission_mode:
        lines.append(f"- Permission mode: {session.permission_mode}")
    thread_id = session.transport_state.get("thread_id")
    if isinstance(thread_id, str) and thread_id:
        lines.append(f"- Thread: {thread_id}")
    commands = sorted(
        {
            f"/{name}"
            for command in _session_slash_commands(adapter, session.id)
            if (name := _slash_command_name(command))
        }
    )
    if commands:
        preview = ", ".join(commands[:12])
        suffix = f", +{len(commands) - 12} more" if len(commands) > 12 else ""
        lines.append(f"- Runtime slash commands: {preview}{suffix}")
    return "\n".join(lines)


def build_plugin() -> ClaudeCodePlugin:
    return ClaudeCodePlugin()


__all__ = [
    "CLAUDE_EFFORT_LEVELS",
    "CLAUDE_PERMISSION_MODES",
    "DEFAULT_CLAUDE_MODELS",
    "ClaudeCodePlugin",
    "ClaudeCodePluginConfig",
    "build_plugin",
]
