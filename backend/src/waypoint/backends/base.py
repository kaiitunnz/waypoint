from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel

from waypoint.backends.capabilities import BackendCapabilities
from waypoint.backends.plugin_config import PluginConfig, PluginLaunchTargetConfig
from waypoint.schemas import CommandCompletion, SessionRecord
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from fastapi import FastAPI

    from waypoint.backends.context_usage_source import ContextUsageSource
    from waypoint.launch_targets import SshLaunchTargetConfig
    from waypoint.runtime import SessionRuntime


def config_dir_for(
    capabilities: BackendCapabilities, launch_env: Mapping[str, str]
) -> str | None:
    """The session's override for the agent's config-dir env var, if any.

    The single resolver for "which config/account dir does this session use":
    an account profile sets the agent's ``config_dir_env_var``
    (``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME``) in ``launch_env``. Every
    per-session operation that touches on-disk agent state (transcript tailing,
    thread lookup/resume, rollout discovery, side-questions, plan-file
    detection) MUST resolve the dir through this — reading the process env or a
    hardcoded default instead makes the op operate on the wrong account's dir
    for a profile-scoped session (the failure mode behind PRs #241/#246).
    """
    key = capabilities.config_dir_env_var
    return launch_env.get(key) if key else None


class ConfigDirNotReadyError(Exception):
    """A profile's config dir isn't ready to launch/resume this agent headlessly.

    Raised by :meth:`ConfigDirValidating.ensure_config_dir_ready` with a terse,
    user-facing reason; the runtime maps it to a 400 before any launch or
    destructive switch step.
    """


class ConfigDirReadiness(BaseModel):
    """A non-raising verdict on whether an account profile's config dir is set up.

    ``ready`` is the same signal :class:`ConfigDirValidating` enforces at
    launch/switch, but reported instead of raised so ``accounts doctor`` can
    surface it alongside the other checks. ``reason`` is a terse, user-facing
    explanation when ``ready`` is ``False`` (``None`` when ready).
    """

    ready: bool
    reason: str | None = None


@runtime_checkable
class ConfigDirReadinessReporting(Protocol):
    """An agent that can report (without raising) whether a config dir is set up.

    The diagnostic sibling of :class:`ConfigDirValidating`. Every agent that owns
    a config-dir env var can implement this so ``accounts doctor`` reports a
    readiness verdict per profile — including codex, which deliberately does
    *not* implement :class:`ConfigDirValidating` (its app-server transport fails
    fast on an unset home rather than hanging, so it needs no launch/switch
    guard) but still benefits from a doctor readiness line based on ``auth.json``
    presence.
    """

    def config_dir_readiness(self, config_dir: str) -> ConfigDirReadiness:
        """Report whether launching/resuming under ``config_dir`` is set up,
        without raising."""
        ...


@runtime_checkable
class ConfigDirValidating(Protocol):
    """An agent that can pre-flight an account profile's config dir.

    Pointing an agent's ``config_dir_env_var`` (``CLAUDE_CONFIG_DIR`` /
    ``CODEX_HOME``) at a dir the CLI treats as first-run can strand a session on
    an interactive setup prompt it can never dismiss headlessly — the claude TUI
    onboarding wizard (theme/login) is the motivating case: a profile aimed at a
    config dir whose ``.claude.json`` has not completed onboarding relaunches
    into the wizard and the tmux/tty-driven turn hangs forever. Codex's default
    app-server transport has no such prompt (an unset home fails fast), so it
    simply does not implement this.

    The runtime narrows to this protocol (``isinstance``) when resolving a
    profile's config-dir env for a *local* launch or switch, and rejects up
    front rather than launching into a hang. An implementer typically also
    implements :class:`ConfigDirReadinessReporting` and defines
    ``ensure_config_dir_ready`` in terms of it (compute the verdict once, raise
    when not ready).
    """

    def ensure_config_dir_ready(self, config_dir: str) -> None:
        """Raise :class:`ConfigDirNotReadyError` if launching or resuming under
        ``config_dir`` would block on an interactive first-run prompt this agent
        cannot clear headlessly; return ``None`` when the dir is ready."""
        ...


@runtime_checkable
class FreshThreadRestarting(Protocol):
    """An agent that can replace an unpersisted native thread after a switch."""

    async def restart_unpersisted_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        """Start a fresh native thread for an already-persisted session row."""
        ...


@runtime_checkable
class DefaultConfigDirProviding(Protocol):
    """An agent that knows its local config directory when no env override exists."""

    def default_config_dir(self) -> str | None:
        """Return the local default config directory, if the agent has one."""
        ...


@runtime_checkable
class PaneSubmitConfirming(Protocol):
    """A plugin that can read its tmux-wrapped TUI's composer to drive input
    reliably, against two failure modes of a raw ``send-keys`` submit:

    - A freshly relaunched pane (reattach after exit, or a model/permission
      restart) is still booting when the message is sent, so the composer does
      not yet exist and the keystrokes are dropped.
    - The composer exists but absorbs the submit ``Enter`` while ingesting the
      paste — e.g. the Claude TUI loading an image pasted by path — leaving the
      message typed but unsent.

    The tmux transport narrows to this protocol (``isinstance``) and, for a
    plugin that satisfies it, waits for :meth:`pane_ready_for_input` before
    pasting, then sends ``Enter`` and confirms the composer cleared via
    :meth:`confirm_pane_submit`, retrying the keystroke if it was swallowed.
    Agents whose composer submits synchronously simply do not implement it and
    keep the single-``Enter`` path.
    """

    def pane_ready_for_input(self, pane_text: str) -> bool:
        """Return whether the wrapped TUI's composer is rendered and able to
        accept input, given a ``capture-pane`` snapshot. False while the pane is
        still booting (no composer drawn yet), so the transport waits rather than
        pasting into a TUI that will drop the keystrokes."""
        ...

    def pane_shows_blocking_dialog(self, pane_text: str) -> bool:
        """Return whether a modal popup (tool-permission approval, trust prompt,
        AskUserQuestion, model/effort picker) is on screen. Such dialogs capture
        ``Enter`` to select an option, so the transport must not paste a message
        or fire the submit ``Enter`` into one — that would silently approve a
        tool or accept a prompt. Agents that cannot read their dialogs off the
        pane return False (the guard is then a no-op for them)."""
        ...

    def confirm_pane_submit(self, pane_text: str, sent_text: str) -> bool:
        """Return whether the just-sent input has left the composer (submitted),
        given a ``capture-pane`` snapshot of the wrapped TUI and the text that
        was sent. Agents that render input literally check that ``sent_text``
        no longer occupies the composer; ones that collapse it (Claude pastes an
        image to an ``[Image]`` chip) check that the composer is empty instead."""
        ...


@runtime_checkable
class BackendPlugin(Protocol):
    """Source of truth for everything backend-specific.

    Steps 3-5 of the refactor migrated each backend's capability
    descriptor, permission catalogue, and control-surface application
    behind this contract. Steps later in the plan extend it with
    lifecycle / thread-discovery / event normalisation methods so the
    runtime can drop the remaining backend literals.
    """

    id: str
    transport_id: str
    label: str
    capabilities: BackendCapabilities
    # Optional agent declaration, read defensively by the registry (a plugin
    # that omits it supports only its own ``transport_id``). ``supported_transports``
    # lists the transport ids the agent can be driven over — its native
    # ``transport_id`` plus any generic pane wrapper it pairs with, e.g.
    # ``("claude_cli", "tmux")`` — and keys the registry's (agent, transport)
    # pair resolution. ``default_transport`` names the one used when a launch
    # request doesn't pin a transport. Generic transport plugins (tmux) leave
    # these at their own transport id. Not Protocol fields: keeping them off the
    # ``runtime_checkable`` surface lets third-party plugins predating them
    # still pass ``isinstance`` and register.
    #
    # ``rate_limit_account(snapshot) -> tuple[str, str] | None`` is another
    # optional method read defensively by the usage dashboard (via
    # ``getattr(plugin, "rate_limit_account", None)``). Agents that scope
    # rate limits to an account (Claude's org/tier, Codex's email/plan)
    # parse their own ``notes`` into ``(account_key, account_label)`` here;
    # plugins that omit it fall back to a session-scoped dashboard bucket.
    # Kept off the Protocol surface for the same predating-plugin reason.
    #
    # ``validate_new_session_selection(runtime, model, effort, launch_target_id)
    # -> None`` is another optional method, mixed in via
    # :class:`DefaultLaunchContract` and read defensively by
    # ``SessionRuntime.create_session`` (via
    # ``getattr(plugin, "validate_new_session_selection", None)``). It is a
    # new-session preflight: agents that can prove a model/effort combination
    # the installed CLI won't honor raise ``ValueError`` to reject the
    # launch; anything they can't judge (an unrecognized or free-text model)
    # must return ``None`` and let the launch proceed. The default is a
    # no-op. Never consulted for resume / set-model / set-effort — only for
    # brand-new sessions.
    # Pydantic model used by the dispatcher in api.py to validate the
    # JSON body of POST /api/backends/{id}/sessions/import. ``None`` for
    # plugins that don't accept thread imports — the dispatcher gates on
    # ``capabilities.supports_thread_import`` first, so this only needs
    # to be set when that capability is True.
    import_request_schema: type[BaseModel] | None
    # Subclass of ``PluginConfig`` that the YAML validator parses
    # ``plugin_configs.<plugin_id>`` into. Plugins without bespoke
    # configuration can point at ``PluginConfig`` itself.
    config_schema: type[PluginConfig]
    # Subclass of ``PluginLaunchTargetConfig`` parsed out of
    # ``ssh_targets[*].plugin_configs.<plugin_id>``. Plugins without
    # per-target knobs beyond ``remote_bin`` point at the base class.
    launch_target_schema: type[PluginLaunchTargetConfig]
    # Env vars the plugin requires on every tmux-wrapped CLI invocation
    # (local and SSH-remote). Merged into the launch command in
    # ``_command_for_backend``; the user-supplied target ``remote_env``
    # overrides on key collision so explicit yaml config still wins.
    extra_env: dict[str, str]

    def transport_view(self, runtime: "SessionRuntime") -> TransportAdapter:
        """Return a TransportAdapter routing send/interrupt/etc. for this plugin."""
        ...

    def validate_permission_mode(self, mode: str | None) -> str | None:
        """Validate a user-supplied permission mode for this backend.

        Returns the canonical mode string when accepted, ``None`` when
        the caller didn't pick one (so the runtime falls back to its
        defaults), and raises ``HTTPException`` for unknown modes.
        """
        ...

    async def apply_permission_mode(
        self, runtime: "SessionRuntime", session: SessionRecord, mode: str
    ) -> None:
        """Apply a validated permission mode mid-session.

        Only invoked when the plugin advertises
        ``supports_set_permission_mode_inline=True``.
        """
        ...

    async def apply_model(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        model: str | None,
    ) -> None:
        """Apply a model swap mid-session.

        Only invoked when the plugin advertises
        ``supports_set_model_inline=True``.
        """
        ...

    async def apply_effort(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        effort: str | None,
    ) -> bool:
        """Apply an effort swap mid-session.

        Returns ``True`` when the runtime should publish a system note
        announcing the swap (e.g. Claude restarts the CLI to pick up a
        new ``--effort``). Codex applies effort silently per turn so
        returns ``False``.
        """
        ...

    def effort_swap_message(self, effort: str | None) -> str:
        """User-visible system note text for an effort swap announcement."""
        ...

    async def list_models(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        include_hidden: bool = False,
        account_profile_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the model catalogue payload served by ``/api/backends/{id}/models``.

        ``account_profile_id`` scopes the discovery to a named account profile's
        config dir (via ``runtime.discovery_env``) so a live catalogue reflects
        the account the session will launch under; ``None`` uses the process
        default. Backends with a static catalogue may ignore it.
        """
        ...

    async def list_command_completions(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        *,
        trigger: str = "/",
        prefix: str = "",
        force_refresh: bool = False,
    ) -> list[CommandCompletion]:
        """Return command or skill completions available in a session."""
        ...

    async def restore_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        """Restore a previously-running session after a runtime restart."""
        ...

    async def list_threads(
        self,
        runtime: "SessionRuntime",
        launch_target_id: str | None = None,
        account_profile_id: str | None = None,
    ) -> list[Any]:
        """Return importable thread summaries for this backend.

        Each plugin returns its own summary type (CodexThreadSummary,
        ClaudeThreadSummary). The API serialises via ``model_dump`` so
        the wire shape is plugin-controlled. ``account_profile_id`` scopes the
        enumeration to a named account profile's config dir (via
        ``runtime.discovery_env``); ``None`` uses the process default.
        """
        ...

    async def import_thread(
        self, runtime: "SessionRuntime", request: Any, *, agent: str | None = None
    ) -> SessionRecord:
        """Import an existing backend-side thread as a Waypoint session.

        ``agent`` is the agent id the imported session is persisted under. It
        differs from ``self.id`` when a sibling plugin drives a pinned
        transport (e.g. the tty-tail driver imports a Claude thread, persisting
        ``backend=claude_code``). ``None`` defaults to ``self.id`` — the
        behavior when no transport is pinned.
        """
        ...

    async def delete_thread(
        self,
        runtime: "SessionRuntime",
        thread_id: str,
        launch_target_id: str | None = None,
        account_profile_id: str | None = None,
    ) -> bool:
        """Delete a resumable backend-side thread's on-disk transcript.

        Returns ``True`` when a transcript matching ``thread_id`` was found
        and removed, ``False`` when none matched. ``launch_target_id`` selects
        the SSH target whose store to delete from (``None`` = local);
        ``account_profile_id`` scopes to a named account profile's config dir
        (via ``runtime.discovery_env``), ``None`` = process default. Only
        invoked for plugins that advertise
        ``capabilities.supports_thread_delete``; the API gates on it first.
        """
        ...

    async def create_session(
        self,
        runtime: "SessionRuntime",
        request: Any,
        *,
        session_id: str,
        launch_target: Any,
        title: str,
        raw_log: Any,
        structured_log: Any,
        git_meta: Any,
        permission_mode: str | None,
        resolved_model: str | None,
        resolved_effort: str | None,
    ) -> SessionRecord:
        """Spawn a new session for this backend."""
        ...

    async def fork_session(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        new_session_id: str,
        title: str,
        raw_log: Path,
        structured_log: Path,
    ) -> SessionRecord:
        """Fork an existing session into a new branch."""
        ...

    async def maybe_handle_input(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        request: Any,
    ) -> SessionRecord | None:
        """Optional pre-send hook for slash routing.

        Return ``None`` to let the runtime forward the user input to
        ``transport.send_input``. Return a populated ``SessionRecord``
        to short-circuit (e.g. Codex's ``/compact`` slash routes
        through ``thread/compact/start`` instead of stdin).
        """
        ...

    async def answer_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        answer: str,
        tool_use_id: str | None,
        answers: list[dict[str, Any]] | None,
    ) -> SessionRecord:
        """Respond to a Claude AskUserQuestion tool call."""
        ...

    async def approve_plan(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        plan_item_id: str,
        decision: str,
        text: str | None,
    ) -> SessionRecord:
        """Apply a plan-approval decision (accept / decline / cancel)."""
        ...

    async def post_approval(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        """Run any side effects triggered by an approval response.

        Claude flips the CLI's permission mode after an ExitPlanMode
        approval; the plugin syncs the runtime + broadcast here so the
        UI pill reflects the new mode.
        """
        ...

    async def fork_side_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        side_question_id: str,
        *,
        new_session_id: str,
        title: str,
        raw_log: Path,
        structured_log: Path,
    ) -> SessionRecord:
        """Promote an open ``/btw`` side-question into a real session.

        Adopts the aside's forked thread as a new managed session (handing off
        the thread, not deleting it) and drops the side-question record.
        Backends without side-question support raise ``HTTPException(400)``.
        """
        ...

    async def dismiss_side_question(
        self,
        runtime: "SessionRuntime",
        session: SessionRecord,
        side_question_id: str,
    ) -> None:
        """Resolve a ``/btw`` side-question: drop its record, delete its forked
        thread, and broadcast the removal. Backends without side-question
        support raise ``HTTPException(400)``.
        """
        ...

    def setup(self, runtime: "SessionRuntime") -> None:
        """One-shot initialisation hook called from ``SessionRuntime.__init__``.

        Plugins use this to build their adapter, hook bundles, and any
        per-process resources. Default is a no-op so plugins that
        don't need bootstrapping (Tmux fallback) opt out.
        """
        ...

    async def shutdown(self, runtime: "SessionRuntime") -> None:
        """Tear down per-process resources owned by this plugin.

        Called from ``SessionRuntime.stop`` so plugins can close their
        adapter (kill subprocesses, drain queues, close SDK clients) in
        the same order they were brought up. Default is a no-op for
        plugins that don't own any background state.
        """
        ...

    def is_available_for_managed_launch(self, runtime: "SessionRuntime") -> bool:
        """Whether the plugin is ready to spawn a fresh managed session.

        Structured backends use this to signal that their adapter
        bootstrap (Claude's hook bundle, a future OAuth handshake, …)
        succeeded. The runtime falls back to the tmux plugin when the
        answer is False, preserving the ``backend == "claude_code"``
        fallback path without naming a specific backend.
        """
        ...

    def remote_executable(self, launch_target: "SshLaunchTargetConfig") -> str:
        """Return the absolute or PATH-resolvable binary name for this
        backend on a given SSH launch target.

        Used by the tmux fallback when wrapping a remote ``claude`` /
        ``codex`` invocation. Plugins typically read
        ``launch_target.remote_bin_for(self.id, self.capabilities.cli_binary)``
        so users can pin a remote install path via the per-target
        ``remote_bins`` mapping. Wrapper plugins that never get
        launched themselves (tmux) can return an empty string — the
        runtime only calls this on the inner backend.
        """
        ...

    async def terminate_session(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        """Tear down any in-process state for ``session``.

        Called before re-restoring an EXITED/ERROR session so the prior
        adapter slot (Claude/Codex stream watchers, subprocess handles)
        is dropped instead of left dangling. Default is a no-op.
        """
        ...

    def native_thread_id(self, session: SessionRecord) -> str | None:
        """Return the backend-native thread/conversation id, if any.

        Generic code never reads ``transport_state`` keys directly; this
        exposes the id a user would pass to the raw CLI (e.g. ``claude
        --resume <id>``) so it can be surfaced for recovery. Plugins
        without a resumable native id return ``None``.
        """
        ...

    def native_thread_artifacts(
        self, session: SessionRecord, config_dir: str | None = None
    ) -> list[Path]:
        """On-disk artifact file(s) needed to resume this session's native
        thread, resolved under ``config_dir`` (the backend's config/state root,
        e.g. a target account profile's ``CLAUDE_CONFIG_DIR``/``CODEX_HOME``).

        ``config_dir=None`` resolves under the process default. Returns ``[]``
        when the thread isn't present under that root — the signal that a
        target profile can't yet see it — or when the backend has no resumable
        native store. Used by account-profile switching to verify/copy the
        transcript before restore.
        """
        ...

    def native_thread_artifact_glob(self, session: SessionRecord) -> str | None:
        """Glob pattern, relative to a config dir, matching this session's
        native thread artifact file(s) on disk (e.g.
        ``projects/*/<uuid>.jsonl``).

        Reuses the same needle :meth:`AgentLaunchContract.conversation_exists`
        checks, so local and remote transcript-availability discovery
        (:mod:`waypoint.backends.transcript_fs`) share one declared pattern
        instead of re-deriving backend-specific path knowledge per IO backend.
        ``None`` when the backend has no resumable native store, or the
        session has no native thread id yet to search for.
        """
        ...

    def on_session_deleted(
        self, runtime: "SessionRuntime", session: SessionRecord
    ) -> None:
        """Hook fired after a session row is deleted from storage.

        Plugins that cache cross-session metadata (e.g. Claude's remote
        thread enumerator) invalidate here so the next listing reflects
        the deletion.
        """
        ...

    def create_context_usage_source(
        self, session: SessionRecord, runtime: "SessionRuntime"
    ) -> "ContextUsageSource | None":
        """Return a background source that publishes context usage, or ``None``.

        Only called for sessions whose active transport has
        ``is_structured == False``.  The runtime starts the returned source as a
        tracked asyncio task and cancels it when the session exits.
        Default returns ``None`` (no-op).
        """
        ...

    def register_routes(self, app: "FastAPI", context: Any) -> None:
        """Optional FastAPI route-registration hook.

        Called once during ``create_app`` after the runtime is built.
        The Claude plugin uses this to mount its PreToolUse approval
        webhook; other plugins can mount internal routes (OpenCode
        webhook receiver, Codex stream proxies) without touching
        ``api.py``.
        """
        ...


@runtime_checkable
class AgentLaunchContract(Protocol):
    """Transport-agnostic launch knowledge an agent exposes.

    A *generic* transport — the tmux pane wrapper and the tty-tail driver —
    drives any agent without knowing which one it is. The agent-specific
    bits of that flow (how to pin model/effort/permission at startup, how to
    resume a thread, how the agent's native conversation id is discovered)
    are the agent's knowledge, not the transport's.

    Historically these lived as ``if backend == "claude_code"`` /
    ``if backend == "codex"`` branches inside ``backends/tmux/plugin.py`` —
    the exact per-backend branching the plugin architecture forbids
    everywhere else. This contract relocates them onto the agent: a generic
    transport calls ``registry.get(session.backend)`` and dispatches through
    these methods, so a new agent gets pane-wrapping for free and tmux holds
    no backend literals.

    Agent plugins satisfy this by mixing in :class:`DefaultLaunchContract`
    and overriding the methods their CLI actually supports.
    """

    def launch_flags(
        self,
        *,
        model: str | None,
        effort: str | None,
        permission_mode: str | None,
    ) -> list[str]:
        """CLI flags that pin model / effort / permission at process start.

        Mirrors the structured-launch flag set the user picks in the launch
        panel, for the interactive CLI a pane wrapper spawns. Omit flags the
        CLI does not accept (e.g. codex has no ``--effort``).
        """
        ...

    def pregenerate_thread_id(self) -> str | None:
        """A thread/conversation id to pass at launch, or ``None``.

        Claude accepts ``--session-id <uuid>`` so the id is known before the
        first turn; codex only reveals its id after the first persist, so it
        returns ``None`` and relies on :meth:`capture_thread_id`.
        """
        ...

    def resume_args(self, thread_id: str, prior_args: list[str]) -> list[str]:
        """Translate launch args into the CLI's resume form.

        Claude prepends ``--resume <id>`` (scrubbing any prior
        ``--session-id`` / ``--resume``); codex prepends the ``resume <id>``
        subcommand. Agents with no resume contract return ``prior_args``
        unchanged.
        """
        ...

    async def conversation_exists(
        self,
        thread_id: str,
        cwd: str,
        launch_target: "SshLaunchTargetConfig | None",
        config_dir: str | None = None,
    ) -> bool:
        """Whether the agent has persisted ``thread_id`` to disk yet.

        Both Claude and Codex defer conversation-file creation until first
        input; resuming a never-written thread makes the CLI exit with "no
        conversation found", so callers gate resume on this. Checks the
        local filesystem, or the remote one over SSH when ``launch_target``
        is set. ``config_dir`` overrides the agent's state root (the value of
        its ``config_dir_env_var`` in the session's launch env, e.g.
        ``CLAUDE_CONFIG_DIR``/``CODEX_HOME``); ``None`` uses the default. A
        pane-wrapping transport that resumes under a switched account profile
        must pass it, or the check reads the default root and the thread is
        never found — forking a new conversation instead of resuming.
        """
        ...

    async def capture_thread_id(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        cwd: str,
        since: datetime,
        launch_target: "SshLaunchTargetConfig | None",
    ) -> None:
        """Best-effort discovery of the native thread id after launch.

        For agents whose id only appears post-launch (codex writes a
        ``rollout-<ts>-<uuid>.jsonl`` on first persist), this polls for it
        and stores ``transport_state.thread_id`` so a later reconnect can
        resume. A no-op for agents that pregenerate the id.
        """
        ...


class DefaultLaunchContract:
    """Inert defaults for :class:`AgentLaunchContract`.

    Agent plugins mix this in and override the methods their CLI supports.
    The defaults are the correct behaviour for an agent with no pane-wrapper
    launch knobs and no resumable thread (the opencode case today): no extra
    flags, no pregenerated id, verbatim resume args, no on-disk thread to
    find. ``claude_code`` and ``codex`` override every method with their real
    logic.
    """

    def launch_flags(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
    ) -> list[str]:
        return []

    def pregenerate_thread_id(self) -> str | None:
        return None

    def resume_args(self, thread_id: str, prior_args: list[str]) -> list[str]:
        return list(prior_args)

    async def conversation_exists(
        self,
        thread_id: str,
        cwd: str,
        launch_target: "SshLaunchTargetConfig | None",
        config_dir: str | None = None,
    ) -> bool:
        return False

    async def capture_thread_id(
        self,
        runtime: "SessionRuntime",
        session_id: str,
        cwd: str,
        since: datetime,
        launch_target: "SshLaunchTargetConfig | None",
    ) -> None:
        return None

    def validate_new_session_selection(
        self,
        runtime: "SessionRuntime",
        model: str | None,
        effort: str | None,
        launch_target_id: str | None,
    ) -> None:
        """New-session preflight: raise to reject a model/effort combo.

        Called once by ``SessionRuntime.create_session``, before the process
        is spawned, so a combination the installed CLI is known not to
        support (e.g. an effort level too new for the detected binary)
        fails fast with a clear message instead of the CLI silently
        rejecting or downgrading the flag. Must raise ``ValueError`` to
        reject; anything else -- including a model this agent doesn't
        recognize (free text is allowed) -- returns ``None`` and lets the
        launch proceed. The default is a no-op: agents override this only
        when they can prove a combination is unsupported.
        """
        return None
