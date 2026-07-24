from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from waypoint.launch_env import LaunchEnv


def _validate_backend_id(value: Any) -> str:
    if isinstance(value, StrEnum):
        value = str(value)
    if not isinstance(value, str) or not value:
        raise TypeError(f"backend id must be a non-empty string, got {value!r}")
    from waypoint.backends.registry import get_registry

    if not get_registry().has_backend(value):
        raise ValueError(f"unknown backend: {value!r}")
    return value


def _validate_transport_id(value: Any) -> str:
    if isinstance(value, StrEnum):
        value = str(value)
    if not isinstance(value, str) or not value:
        raise TypeError(f"transport id must be a non-empty string, got {value!r}")
    from waypoint.backends.registry import get_registry

    if not get_registry().has_transport(value):
        raise ValueError(f"unknown transport: {value!r}")
    return value


# Annotated string types validated against the live plugin registry.
# Schemas use these so adding a new backend never requires editing this
# module: any plugin registered via ``backends/bootstrap.py`` is
# automatically accepted as a valid id.
BackendId = Annotated[str, BeforeValidator(_validate_backend_id)]
SessionTransportId = Annotated[str, BeforeValidator(_validate_transport_id)]


class SessionSource(StrEnum):
    MANAGED = "managed"
    ATTACHED_TMUX = "attached_tmux"
    # The personal-assistant singleton. Created and kept alive by the
    # runtime, protected from deletion/termination via the public API,
    # and surfaced on its own UI page rather than the session list.
    ASSISTANT = "assistant"
    # A throwaway one-shot session driving a generic non-interactive agent
    # task (e.g. the NL-insight summarizer's ``runtime.run_oneshot``). Lives
    # for one turn and is torn down (terminate + delete) by its caller;
    # excluded from the session list the same way ``ASSISTANT`` is.
    TELEMETRY = "telemetry"


class LaunchMode(StrEnum):
    AUTO = "auto"
    DIRECT = "direct"
    TMUX_WRAPPER = "tmux_wrapper"


class SessionStatus(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    WAITING_INPUT = "waiting_input"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    EXITED = "exited"
    ERROR = "error"


class EventKind(StrEnum):
    USER_INPUT = "user_input"
    AGENT_OUTPUT = "agent_output"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUEST = "approval_request"
    STATUS_UPDATE = "status_update"
    SYSTEM_NOTE = "system_note"
    RAW_TERMINAL_CHUNK = "raw_terminal_chunk"


class CompletionDispatch(StrEnum):
    FRONTEND_CONTROL = "frontend_control"
    PLAIN_TEXT = "plain_text"
    BACKEND_COMMAND = "backend_command"
    STRUCTURED_SKILL = "structured_skill"


class CommandCompletion(BaseModel):
    id: str
    trigger: str
    replacement: str
    name: str
    description: str | None = None
    kind: str
    source: str
    dispatch: CompletionDispatch
    argument_hint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionCommandInvocation(BaseModel):
    completion_id: str
    name: str
    arguments: str = ""
    dispatch: CompletionDispatch
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionInputItem(BaseModel):
    type: Literal["text", "skill", "mention"]
    text: str | None = None
    name: str | None = None
    path: str | None = None


class AttachmentKind(StrEnum):
    IMAGE = "image"
    FILE = "file"


class AttachmentSpec(BaseModel):
    # Server-issued handle for an uploaded blob. The frontend receives this
    # from the upload endpoint and later references the attachment by ``id``
    # when sending input; the runtime resolves the id back to a host path
    # server-side, so the absolute path is never trusted from the client.
    id: str
    filename: str
    mime: str
    size: int
    kind: AttachmentKind


class SessionCompletionsResponse(BaseModel):
    completions: list[CommandCompletion] = Field(default_factory=list)
    refreshing: bool = False


class SessionContextUsage(BaseModel):
    used_tokens: int
    context_window_tokens: int | None = None
    updated_at: datetime
    source: BackendId
    breakdown: dict[str, int] = Field(default_factory=dict)


# The 5 disjoint category buckets an aggregate (API-facing totals dict) may
# carry. The ledger itself keeps storing each backend's raw, overlapping
# native keys (``input_tokens``/``cachedInputTokens``/``cache.read``/…,
# normalized only at each plugin's parse boundary) and is never backfilled
# onto this vocabulary. The one documented exception to "never infer one
# category from another" is the telemetry read layer's ``unify_tokens``
# (``telemetry/tokens.py``), which folds those native keys onto these 5
# buckets for display via a deterministic, source-keyed subset subtraction —
# not a guess — so every aggregate response's totals always sum safely.
TOKEN_USAGE_CATEGORIES = (
    "fresh_input",
    "cache_read",
    "cache_write",
    "output",
    "reasoning",
)

TokenUsageCoverage = Literal["entire_waypoint_session", "tracked_since", "partial"]


class SessionTokenUsage(BaseModel):
    """Cumulative per-turn token work tracked across a Waypoint session.

    Distinct from ``SessionContextUsage``: that is the latest provider-reported
    context-window occupancy (``used / window``); this is the sum of per-turn
    provider-reported token work over the session's tracked life. Because a
    provider re-sends prior context on later turns, this total can exceed the
    context window many times over — it is never a percentage of the window and
    never drives the context warning colour.
    """

    source: BackendId
    tracked_turns: int
    totals: dict[str, int] = Field(default_factory=dict)
    # Provider-safe grand total across tracked turns, when the plugin can supply
    # one whose categories do not overlap (e.g. Codex ``totalTokens``). ``None``
    # means the UI shows category totals without a synthesized grand total.
    display_total_tokens: int | None = None
    observed_from: datetime
    complete_through: datetime
    backfilled_through: datetime | None = None
    coverage: TokenUsageCoverage
    coverage_note: str | None = None
    updated_at: datetime


class TokenUsageRecord(BaseModel):
    """One durable per-turn ledger row (storage/ingestion input).

    ``record_id`` is the plugin-owned stable turn/message identity; the ledger's
    unique key is ``(session_id, source, record_id)`` so retransmission and
    tmux-artifact replay upsert the same row rather than double-counting.
    """

    record_id: str
    source: BackendId
    observed_at: datetime
    totals: dict[str, int] = Field(default_factory=dict)
    display_total_tokens: int | None = None
    # The concrete model/effort in effect for this turn (telemetry's "actual
    # model at turn time", FR-4). ``None`` when the plugin can't resolve one.
    model: str | None = None
    effort: str | None = None


class TokenUsageInit(BaseModel):
    """Aggregate seed applied only when the first record for a session lands.

    The runtime computes this from session context (source + adopted-thread
    marker) so storage stays mechanical; ignored once an aggregate exists.
    """

    coverage: TokenUsageCoverage
    observed_from: datetime
    coverage_note: str | None = None


class UsageWindow(BaseModel):
    id: str
    label: str
    used_percent: float
    used_tokens: int | None = None
    limit_tokens: int | None = None
    remaining_tokens: int | None = None
    window_minutes: int | None = None
    resets_at: datetime | None = None
    reset_description: str | None = None


class SessionRateLimitUsage(BaseModel):
    source: BackendId
    updated_at: datetime
    windows: list[UsageWindow] = Field(default_factory=list)
    credits_remaining: float | None = None
    credits_currency: str | None = None
    notes: list[str] = Field(default_factory=list)


# ── Session-independent usage providers (Lumid in v1) ──
#
# Provider-neutral models shared by the usage_providers package, the dashboard
# composer, the API response, and telemetry ingestion. They live here (not in
# usage_providers/) so ``schemas`` never imports the provider package — the
# provider package depends on ``schemas``, like the rest of the codebase. All
# ids are plain ``str`` (never ``BackendId``, which rejects unregistered ids).

# Coarse, safe result state for a provider refresh. Never carries a raw token,
# email, header, or upstream body.
ProviderErrorState = Literal[
    "missing_token",
    "identity_failed",
    "permission_denied",
    "usage_unavailable",
    "no_matching_usage",
    "network",
    "unknown",
]


class ProviderRateLimitUsage(BaseModel):
    """A provider account's rate-limit snapshot. Mirrors ``SessionRateLimitUsage``
    but ``source_id`` is an unconstrained provider string, not a ``BackendId``."""

    source_id: str
    updated_at: datetime
    windows: list[UsageWindow] = Field(default_factory=list)
    credits_remaining: float | None = None
    credits_currency: str | None = None
    notes: list[str] = Field(default_factory=list)


class ProviderModelUsage(BaseModel):
    model: str
    tokens: int | None = None
    cost: float | None = None


class ProviderUsageMetadata(BaseModel):
    requests_7d: int | None = None
    last_ts: datetime | None = None
    model_breakdown: list[ProviderModelUsage] | None = None
    total_cost_7d: float | None = None


class ProviderBucketHealth(BaseModel):
    last_success_at: datetime | None = None
    stale: bool = False


class ProviderUsageSnapshot(BaseModel):
    provider_id: str
    provider_type: str
    account_key: str
    account_label: str
    snapshot: ProviderRateLimitUsage
    metadata: ProviderUsageMetadata = Field(default_factory=ProviderUsageMetadata)
    observed_at: datetime
    last_success_at: datetime


class ProviderRefreshResult(BaseModel):
    provider_id: str
    ok_count: int = 0
    error_count: int = 0
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    errors: list[ProviderErrorState] = Field(default_factory=list)


class ProviderUsageStatus(BaseModel):
    provider_id: str
    provider_type: str
    provider_label: str
    enabled: bool
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    stale: bool = False
    result_counts: dict[str, int] = Field(default_factory=dict)
    error_counts: dict[str, int] = Field(default_factory=dict)


# ── Usage dashboard: discriminated union of session + provider buckets ──


class SessionUsageDashboardBucket(BaseModel):
    origin: Literal["session"] = "session"
    backend: BackendId
    account_key: str
    account_label: str
    snapshot: SessionRateLimitUsage
    session_ids: list[str] = Field(default_factory=list)


class ProviderUsageDashboardBucket(BaseModel):
    origin: Literal["provider"] = "provider"
    provider_id: str
    provider_type: str
    provider_label: str
    account_key: str
    account_label: str
    snapshot: ProviderRateLimitUsage
    metadata: ProviderUsageMetadata = Field(default_factory=ProviderUsageMetadata)
    health: ProviderBucketHealth = Field(default_factory=ProviderBucketHealth)
    session_ids: list[str] = Field(default_factory=list)


UsageDashboardBucket = Annotated[
    SessionUsageDashboardBucket | ProviderUsageDashboardBucket,
    Field(discriminator="origin"),
]


class UsageDashboardResponse(BaseModel):
    buckets: list[UsageDashboardBucket] = Field(default_factory=list)
    providers: list[ProviderUsageStatus] = Field(default_factory=list)


class SessionRecord(BaseModel):
    id: str
    backend: BackendId
    source: SessionSource
    transport: SessionTransportId = "tmux"
    title: str
    cwd: str
    launch_target_id: str | None = None
    launch_mode: LaunchMode = LaunchMode.AUTO
    repo_name: str | None = None
    branch: str | None = None
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    last_event_at: datetime
    # Per-plugin opaque state, persisted as a JSON column. Each plugin
    # decides what goes in here — Codex / Claude store ``thread_id``;
    # the tmux fallback stores ``tmux_session`` / ``tmux_window`` /
    # ``tmux_pane`` / ``pid``. Generic code (runtime, storage, API)
    # never reads individual keys; only plugins do.
    transport_state: dict[str, Any] = Field(default_factory=dict)
    raw_log_path: str
    structured_log_path: str
    pinned_at: datetime | None = None
    # The session that spawned this one, if any. Set when a session is created
    # by an agent running inside another session (the CLI stamps it from
    # ``WAYPOINT_SESSION_ID``). Used to inherit the spawner's permission mode
    # and to identify subagent sessions. ``None`` for user/top-level sessions.
    spawner_session_id: str | None = None
    worktree_path: str | None = None
    permission_mode: str | None = None
    model: str | None = None
    # The concrete model id the backend actually resolved and ran (e.g.
    # ``claude-sonnet-5``), as reported by the CLI/API at launch time.
    # Distinct from ``model``, which is the user's *selection* (e.g. the
    # alias ``sonnet``) and is what relaunch/set-model uses. Display should
    # prefer this field so a session's shown model doesn't drift when a
    # catalogue update changes what an alias currently resolves to.
    resolved_model: str | None = None
    effort: str | None = None
    args: list[str] = Field(default_factory=list)
    # Backend-specific structured overrides distinct from raw ``args``.
    # Codex uses this for ``--config K=V`` entries; other backends ignore
    # the field. Empty list = none.
    config_overrides: list[str] = Field(default_factory=list)
    # Environment variables applied when the agent process was launched.
    # Excluded from public dumps because values commonly contain secrets; the
    # runtime still uses the field internally for restore/relaunch.
    launch_env: LaunchEnv = Field(default_factory=dict, exclude=True)
    context_usage: SessionContextUsage | None = None
    session_token_usage: SessionTokenUsage | None = None
    rate_limit_usage: SessionRateLimitUsage | None = None
    # Free-form user/agent labels for grouping and selective teardown, e.g.
    # ``{"role": "backend-lead"}`` or ``{"overflow": ""}`` (a bare tag stores an
    # empty value). Set at launch or via ``sessions tag``; filtered by
    # ``sessions list --tag`` / ``sessions reap --tag``.
    tags: dict[str, str] = Field(default_factory=dict)
    # Provenance for launches created from a session preset. Audit/display hints
    # only — the resolved launch settings are snapshotted onto the record, so
    # later preset edits/deletes do not affect an existing session.
    preset_id: str | None = None
    preset_name: str | None = None
    # Account/config-dir profile this session was launched under. Non-secret
    # display/audit hints; the profile's config-dir is snapshotted into the
    # private launch_env, so the account is bound even if config changes later.
    account_profile_id: str | None = None
    account_profile_label: str | None = None
    # Server-owned provenance from the last account probe (launch, switch,
    # reattach, or boot-restore) — distinct from the client-selected
    # ``account_profile_id``/``label`` above. ``key`` and ``label`` are
    # diagnostic-only (the label is frequently an OAuth email) and excluded
    # from the public dump; ``probed_at`` is a bare timestamp and safe to show
    # as "account last verified <ago>".
    verified_account_key: str | None = Field(default=None, exclude=True)
    verified_account_label: str | None = Field(default=None, exclude=True)
    verified_account_probed_at: datetime | None = None


class EventRecord(BaseModel):
    id: int | None = None
    session_id: str
    ts: datetime
    kind: EventKind
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    sequence: int


class BoardEntry(BaseModel):
    # A single blackboard row. With ``key`` unset the entry is an append-log
    # post (ordered, never overwritten); with ``key`` set it is a key/value
    # cell that the latest post for that ``(channel, key)`` overwrites in place.
    id: int
    channel: str
    # The session that posted this, stamped by the CLI from
    # ``WAYPOINT_SESSION_ID``. ``None`` when posted outside a session (a user
    # via the frontend, an ad-hoc CLI call). Keyed cells authored by a session
    # are pruned on session delete; keyless log posts are durable history and
    # survive the session row being removed.
    author_session_id: str | None = None
    # Snapshot of the authoring session's title at post time. Stays readable
    # after the session row is deleted.
    author_label: str | None = None
    key: str | None = None
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    # Set when the post's text or metadata was edited in place; ``None`` while
    # the post is untouched since it was first written.
    edited_at: datetime | None = None


class BoardChannel(BaseModel):
    channel: str
    entry_count: int
    last_created_at: datetime


class BoardPostRequest(BaseModel):
    text: str
    # When set, upsert the ``(channel, key)`` cell instead of appending.
    key: str | None = None
    author_session_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BoardEntryUpdateRequest(BaseModel):
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    # When True, merge ``metadata`` into the entry's existing metadata (patch
    # semantics) instead of replacing the whole blob. Keys in ``unset`` are
    # removed from the result in either mode.
    merge: bool = False
    unset: list[str] = Field(default_factory=list)
    # The editing session, so its own board-update wake self-excludes (mirrors
    # BoardPostRequest); ``None`` wakes every matching subscriber.
    author_session_id: str | None = None


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: datetime


class AccountProfileMeta(BaseModel):
    """Public, redacted account-profile metadata.

    Display id/label plus the config-dir env var key the profile maps to. Never
    carries the config-dir path, expected account key, or transcript policy.
    """

    id: str
    label: str
    config_dir_key: str


class AccountProbeResult(BaseModel):
    """The account a backend authenticates as under a given config dir.

    ``account_key`` is the stable identity the runtime uses to accept a profile
    switch and match a profile's ``expected_account_key``; ``account_label`` is
    a human display string. Produced by probing the account's rate-limit
    endpoint and mapping the snapshot to an account via the plugin.
    """

    account_key: str
    account_label: str | None = None
    source: Literal["oauth", "api", "cli", "unknown"] = "oauth"


class ProfileCheck(BaseModel):
    """One line of an ``accounts doctor`` per-profile checklist.

    ``ok`` is the pass/fail verdict; ``detail`` is a terse human explanation.
    A check may be reported ``ok=True`` with a ``detail`` of ``"n/a"`` /
    ``"skipped"`` when it doesn't apply (e.g. a transcript check on a
    non-``symlink_shared`` profile, or a filesystem check on a remote target).
    """

    name: str
    ok: bool
    detail: str | None = None


class ProfileDoctorReport(BaseModel):
    """The ``accounts doctor`` verdict for a single account profile.

    ``ok`` is the conjunction of every check. Config-dir paths and account keys
    appear in check details only when the caller asked for them via the
    diagnostic flags (``show_paths`` / ``show_key``); otherwise they stay
    redacted per the phase-1 rules.
    """

    backend: str
    profile: str
    label: str
    ok: bool
    checks: list[ProfileCheck]


class LaunchTargetSummary(BaseModel):
    id: str
    name: str
    kind: str = "ssh"
    supported_backends: list[BackendId] = Field(default_factory=list)
    default_backend: BackendId = "codex"
    default_cwd: str | None = None
    auth: Literal["key", "password"] = "key"
    # Live ControlMaster state; only meaningful when ``auth == "password"``.
    connected: bool = False
    # Effective launch-env defaults keyed by backend id for this target.
    default_launch_env_by_backend: dict[BackendId, LaunchEnv] = Field(
        default_factory=dict
    )
    # Target-merged redacted account-profile metadata keyed by agent backend id.
    # Only backends that host profiles (claude_code, codex) appear; each entry is
    # {id, label, config_dir_key} — no paths, keys, or transcript policy.
    account_profiles_by_backend: dict[BackendId, list[AccountProfileMeta]] = Field(
        default_factory=dict
    )


class LaunchTargetConnectRequest(BaseModel):
    password: str


class LaunchTargetConnectResponse(BaseModel):
    target_id: str
    connected: bool
    detail: str | None = None


class AssistantSummary(BaseModel):
    session_id: str
    backend: BackendId
    # Transport the assistant is driven over, so the UI can label it (Chat /
    # Emulated / Terminal) and preselect it in the settings popover.
    transport: SessionTransportId
    # Backend-native conversation id (e.g. the value for `claude --resume`).
    # Surfaced alongside ``session_id`` so a user can recover the thread
    # outside the app. ``None`` when the backend has no resumable id.
    native_thread_id: str | None = None
    # Redacted account/config profile identity of the live assistant. This
    # deliberately excludes its config-dir path and account key.
    account_profile_id: str | None = None
    account_profile_label: str | None = None
    status: SessionStatus
    # Whether the backend can revive this thread after it exits. Drives the
    # assistant UI's choice between offering Reattach and only Clear context.
    supports_reattach: bool = False


class AssistantResetRequest(BaseModel):
    # Rebuild the assistant on a fresh thread. ``backend`` switches the coding
    # agent (``None`` keeps the current one); the rest seed the new thread,
    # where ``None`` means the backend's default. ``transport`` repins the
    # interface (``None`` keeps the current one on a clear, or the agent's
    # default on a backend switch).
    backend: BackendId | None = None
    transport: SessionTransportId | None = None
    # Omitted means infer from the current assistant for a same-backend reset;
    # explicit null means launch under the backend's default account.
    account_profile_id: str | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None


class AssistantAttachRequest(BaseModel):
    # Adopt an existing backend-native thread as the assistant. ``thread_id`` is
    # the value surfaced by GET /api/backends/{backend}/threads.
    backend: BackendId
    thread_id: str
    launch_target_id: str | None = None
    # The profile that owns the thread being imported.
    account_profile_id: str | None = None


PresetName = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class SessionPresetSpec(BaseModel):
    # Reusable launch defaults. All fields are optional so partial presets can
    # exist; a resolved launch must still satisfy the non-null request rules.
    # ``backend``/``transport`` are plain strings (not registry-validated ids) so
    # a preset stays listable/editable/deletable after a plugin is removed or
    # renamed; the resolver validates them only when applying the preset.
    #
    # Deliberately excludes ``cwd`` and ``title``: those are per-launch specifics
    # (which repo, what to call this run), not reusable launch defaults, so the
    # launch surfaces always supply them explicitly. ``extra="ignore"`` (Pydantic
    # default) means presets persisted with older cwd/title keys drop them on load.
    backend: str | None = None
    launch_target_id: str | None = None
    launch_mode: LaunchMode | None = None
    transport: str | None = None
    args: list[str] = Field(default_factory=list)
    config_overrides: list[str] = Field(default_factory=list)
    launch_env: LaunchEnv = Field(default_factory=dict)
    permission_mode: str | None = None
    model: str | None = None
    effort: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    # Preset the account/config-dir profile to launch under. Applying the preset
    # selects this profile first, so model/thread lists are fetched profile-scoped.
    account_profile_id: str | None = None


class SessionPresetRecord(BaseModel):
    id: str
    name: str
    description: str | None = None
    spec: SessionPresetSpec
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


class SessionPresetSpecSummary(BaseModel):
    # Redacted spec for read surfaces: ``launch_env`` values are omitted and only
    # the keys are exposed, so preset secrets never ride in list / bootstrap
    # payloads. Full values come from GET .../{id}?include_secret_values=true.
    backend: str | None = None
    launch_target_id: str | None = None
    launch_mode: LaunchMode | None = None
    transport: str | None = None
    args: list[str] = Field(default_factory=list)
    config_overrides: list[str] = Field(default_factory=list)
    launch_env_keys: list[str] = Field(default_factory=list)
    permission_mode: str | None = None
    model: str | None = None
    effort: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    account_profile_id: str | None = None


class SessionPresetSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    spec: SessionPresetSpecSummary
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


class SessionPresetCreateRequest(BaseModel):
    name: PresetName
    description: str | None = None
    spec: SessionPresetSpec = Field(default_factory=SessionPresetSpec)
    is_default: bool = False


class SessionPresetUpdateRequest(BaseModel):
    # PATCH semantics: only fields present in the body change. A provided ``spec``
    # is merged field-by-field using its ``model_fields_set``, so fields the
    # client omits (e.g. ``tags``) are preserved on the stored preset.
    name: PresetName | None = None
    description: str | None = None
    spec: SessionPresetSpec | None = None
    is_default: bool | None = None


class SessionPresetListResponse(BaseModel):
    presets: list[SessionPresetSummary] = Field(default_factory=list)
    default_preset_id: str | None = None


class WakeSubscription(BaseModel):
    # A session's standing request to be woken (a content-free ``handle_input``)
    # when a matching board channel or the inbox mutates. ``channel_globs`` are
    # fnmatch patterns over channel names; a non-empty ``kinds`` wakes only on a
    # board post whose ``kind=`` meta is listed (empty ``kinds`` wakes on all).
    id: str
    session_id: str
    channel_globs: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    wake_on_inbox: bool = False
    created_at: datetime


class WakeRegisterRequest(BaseModel):
    channel_globs: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    wake_on_inbox: bool = False


class WakeSubscriptionListResponse(BaseModel):
    subscriptions: list[WakeSubscription] = Field(default_factory=list)


class ManagerTicketState(StrEnum):
    # The 13 states of a ticket in the Waypoint Manager state machine. The three
    # terminals are ``MERGED``, ``DEFERRED``, ``ABANDONED``; every other state has
    # outgoing edges in the transition table (``waypoint.manager._ADJACENCY``).
    INTAKE = "intake"
    TRIAGED = "triaged"
    SPEC_PENDING = "spec_pending"
    SPEC_REVIEW = "spec_review"
    READY = "ready"
    DELEGATED = "delegated"
    BUILDING = "building"
    BLOCKED = "blocked"
    REVIEW_REQUESTED = "review_requested"
    REVISING = "revising"
    MERGED = "merged"
    DEFERRED = "deferred"
    ABANDONED = "abandoned"


class ManagerTicketScale(StrEnum):
    TRIVIAL = "trivial"
    SUBSTANTIAL = "substantial"


class ManagerTicket(BaseModel):
    # One record per ticket. Filterable columns (id/manager_id/priority/state/
    # scale/version/timestamps) are denormalized in storage; the whole model is
    # the source of truth (persisted as a JSON payload blob).
    id: str
    # The manager this ticket belongs to (partition key); one manager per repo.
    manager_id: str = ""
    title: str
    priority: str = "p2"
    kind: str | None = None
    scale: ManagerTicketScale | None = None
    state: ManagerTicketState = ManagerTicketState.INTAKE
    # Coarse code paths the ticket is expected to touch (globs); refined by a spec.
    footprint: list[str] = Field(default_factory=list)
    is_partial: bool = False
    spec_ref: str | None = None
    # Deterministic per-ticket lead title; the spawn dedup key. Unique across all
    # non-terminal tickets (server-enforced invariant).
    intended_lead_title: str | None = None
    lead_session_id: str | None = None
    # The ticket's branch, checked out in the manager's shared working tree while
    # the ticket occupies it (there is no per-ticket sibling worktree).
    branch: str | None = None
    pr_url: str | None = None
    # The inbox item id of the ticket's current human gate, recorded when the gate
    # posts and cleared on any non-self transition, so the answer read scopes to the
    # current episode and never resolves an earlier gate's answer.
    inbox_item_id: str | None = None
    # Initial-delegate spawn-failure budget (distinct from ``lead_restarts``).
    attempts: int = 0
    # Post-work lead-death resume budget (distinct from ``attempts``).
    lead_restarts: int = 0
    deps: list[str] = Field(default_factory=list)
    # Set on entry to a genuinely awaiting-human state; cleared when the ticket
    # leaves it (so a latency timeout only counts real human waits).
    awaiting_since: datetime | None = None
    created_at: datetime
    updated_at: datetime
    version: int = 0


class ManagerRenderContext(BaseModel):
    # Persisted at `manager init`, which bakes the static placeholder values into
    # the compiled templates under `templates_dir`. These are what `manager render`
    # still needs at use time: `templates_dir` to locate a compiled template, and
    # the two channel fields to fetch a ticket's board cell and compute its channel.
    templates_dir: str = ""
    tickets_channel: str = ""
    ticket_channel_prefix: str = ""


class ManagerConfig(BaseModel):
    # The machine-relevant subset of the project manifest that drives the
    # server-side scheduler invariants so a drifting manager context cannot enact
    # an illegal step, plus the render context persisted at init for `manager
    # render`.
    # Server-minted opaque manager id (``mgr-<hex>``). Empty until `init` mints or
    # resolves it; the DB/API key that partitions every manager's state.
    id: str = ""
    # Human-facing project label (display only; not unique).
    project: str = ""
    # The manager's git toplevel. Unique across managers (one manager per repo);
    # the CLI resolves the target manager from the current repo via this field.
    repo_dir: str = ""
    max_delegate_attempts: int = Field(default=3, ge=0)
    max_lead_restarts: int = Field(default=3, ge=0)
    backoff_seconds: int = Field(default=60, ge=0)
    human_latency_hours: int = Field(default=72, ge=0)
    priority_levels: list[str] = Field(default_factory=lambda: ["p0", "p1", "p2", "p3"])
    trunk: str = "main"
    # The session that ran `manager init` (the manager itself). Deleting that
    # session cascades a deinit so no orphaned backlog state lingers behind it.
    owner_session_id: str | None = None
    render_context: ManagerRenderContext | None = None


class ManagerTreeState(BaseModel):
    # Derived, never stored: the single shared working tree, held by at most one
    # ticket from delegate through terminal (delegated..review_requested).
    free: bool
    held_by: str | None = None  # the ticket holding the tree, when not free


class ReconcileIntake(BaseModel):
    id: int
    author_session_id: str | None = None
    text: str = ""
    # The intake post's `priority` meta, surfaced when it names a configured level.
    priority: str | None = None


class ReconcileDeadLead(BaseModel):
    ticket_id: str
    state: ManagerTicketState
    lead_session_id: str | None = None
    lead_status: str | None = None  # None = no such session, else its terminal status


class ReconcileLatencyTimeout(BaseModel):
    ticket_id: str
    state: ManagerTicketState
    # The wait is measured from the current gate item's post (falling back to the
    # awaiting entry when no item exists), so a re-opened gate earns a fresh wait.
    waiting_since: datetime
    hours_elapsed: float


class ReconcileStaleGate(BaseModel):
    # An awaiting-human ticket whose gate inbox item is absent — the recorded
    # ``inbox_item_id`` is empty (a crash between the awaiting transition and the
    # inbox post) or names an item that no longer exists.
    ticket_id: str
    state: ManagerTicketState
    awaiting_since: datetime | None = None


class ReconcileFinalizePending(BaseModel):
    # A terminal ticket that reached the tree (still carries its branch) but was not
    # finalized — a crash between recording the terminal and reaping the subtree.
    ticket_id: str
    state: ManagerTicketState
    branch: str | None = None
    lead_session_id: str | None = None


class ReconcileResolvedGate(BaseModel):
    # An awaiting-human ticket whose recorded gate item is resolved (the human
    # answered) but whose transition has not happened — a re-spec deferred by the
    # busy single ``spec_pending`` slot, or a crash between the answer and the
    # transition. Re-driving the gate handler re-fires the deferred transition.
    ticket_id: str
    state: ManagerTicketState
    inbox_item_id: str | None = None


class ManagerReconcileReport(BaseModel):
    unregistered_intake: list[ReconcileIntake] = Field(default_factory=list)
    dead_leads: list[ReconcileDeadLead] = Field(default_factory=list)
    latency_timeouts: list[ReconcileLatencyTimeout] = Field(default_factory=list)
    stale_gates: list[ReconcileStaleGate] = Field(default_factory=list)
    finalize_pending: list[ReconcileFinalizePending] = Field(default_factory=list)
    resolved_gates: list[ReconcileResolvedGate] = Field(default_factory=list)


class ManagerTicketTransitions(BaseModel):
    ticket_id: str
    priority: str
    state: ManagerTicketState
    legal_transitions: list[ManagerTicketState] = Field(default_factory=list)


class ManagerRecommendedAction(BaseModel):
    ticket_id: str
    from_state: ManagerTicketState
    to_state: ManagerTicketState
    event: str
    reason: str


class TicketCreateRequest(BaseModel):
    title: str
    id: str | None = None
    priority: str = "p2"
    kind: str | None = None
    scale: ManagerTicketScale | None = None
    footprint: list[str] = Field(default_factory=list)
    deps: list[str] = Field(default_factory=list)


class TicketTransitionRequest(BaseModel):
    # Transition by target state (matching the RFC CLI ``--to <state>``); the
    # transition table is the from→{to} adjacency and per-edge guards/meta encode
    # the events. ``reason`` distinguishes edges the RFC labels with two events
    # sharing one (from, to) — e.g. reject vs latency-timeout, done vs partial.
    to: ManagerTicketState
    reason: str | None = None
    scale: ManagerTicketScale | None = None
    kind: str | None = None
    footprint: list[str] | None = None
    spec_ref: str | None = None
    intended_lead_title: str | None = None
    lead_session_id: str | None = None
    branch: str | None = None
    pr_url: str | None = None
    is_partial: bool | None = None
    deps: list[str] | None = None


class TicketUpdateRequest(BaseModel):
    # Edit ticket metadata without a state transition (priority, footprint,
    # spec/lead refs). State changes go through ``TicketTransitionRequest``.
    priority: str | None = None
    kind: str | None = None
    scale: ManagerTicketScale | None = None
    footprint: list[str] | None = None
    deps: list[str] | None = None
    spec_ref: str | None = None
    intended_lead_title: str | None = None
    lead_session_id: str | None = None
    branch: str | None = None
    pr_url: str | None = None
    inbox_item_id: str | None = None
    is_partial: bool | None = None
    # Human-gated override: zero the delegate ``attempts`` budget so a ticket
    # blocked on repeated spawn failures can be retried after a config fix.
    reset_attempts: bool = False
    # Human-gated override: zero the ``lead_restarts`` budget so a ticket blocked
    # on a lead that kept dying can be retried after the cause is fixed.
    reset_lead_restarts: bool = False


class ManagerInitRequest(BaseModel):
    config: ManagerConfig = Field(default_factory=ManagerConfig)


class ManagerNextResponse(BaseModel):
    tree: ManagerTreeState
    tickets: list[ManagerTicketTransitions] = Field(default_factory=list)
    recommended: ManagerRecommendedAction | None = None


class ManagerStateResponse(BaseModel):
    config: ManagerConfig | None = None
    tree: ManagerTreeState
    tickets: list[ManagerTicket] = Field(default_factory=list)


class ManagerSummary(BaseModel):
    # One entry per initialized manager, for the instance-wide manager list
    # (the board's per-project switcher). Counts are derived, never stored.
    id: str
    project: str = ""
    repo_dir: str = ""
    owner_session_id: str | None = None
    ticket_count: int = 0
    # Tickets in a genuinely awaiting-human state (spec_review/blocked/
    # review_requested) — the switcher's per-project attention dot.
    attention_count: int = 0


class ManagerListResponse(BaseModel):
    managers: list[ManagerSummary] = Field(default_factory=list)


class MeResponse(BaseModel):
    authenticated: bool = True
    default_backend: BackendId = "codex"
    default_cwd: str = "~/"
    launch_targets: list[LaunchTargetSummary] = Field(default_factory=list)
    # Plugin catalogue mirrored from `/api/backends`; lets `/api/me`
    # consumers (the frontend's bootstrap, auth shell) hydrate the
    # backend picker without a second round-trip.
    backends: list[dict[str, Any]] = Field(default_factory=list)
    # The personal-assistant singleton, when enabled. The frontend uses
    # this to locate and render the dedicated assistant page.
    assistant: AssistantSummary | None = None
    # Redacted session presets + the default preset id, so the launch panel can
    # populate its selector without a second round-trip. Env values are omitted.
    session_presets: list[SessionPresetSummary] = Field(default_factory=list)
    default_preset_id: str | None = None
    # Whether usage telemetry is enabled. The frontend uses this capability to
    # decide whether to offer the dashboard entry point and to short-circuit
    # the `/telemetry` page to its disabled state before any telemetry fetch.
    telemetry_enabled: bool = False


class EventsPageResponse(BaseModel):
    events: list[EventRecord] = Field(default_factory=list)
    has_more: bool = False
    # The session's most recent todo/task snapshot, populated only in tail
    # mode so the frontend's task dock stays visible even when the latest
    # todo predates the loaded transcript window. ``None`` when the session
    # has no todos or when paginating older pages.
    latest_todo: EventRecord | None = None


class SessionCreateRequest(BaseModel):
    backend: BackendId
    cwd: str
    launch_target_id: str | None = None
    launch_mode: LaunchMode = LaunchMode.AUTO
    # Pins the transport the agent is driven over. ``None`` keeps the
    # ``launch_mode``-derived behavior; an explicit transport takes
    # precedence over ``launch_mode`` and must be in the chosen agent's
    # ``supported_transports`` (the runtime rejects mismatches with 400).
    transport: SessionTransportId | None = None
    title: str | None = None
    args: list[str] = Field(default_factory=list)
    config_overrides: list[str] = Field(default_factory=list)
    # Private in responses; schedule rows are public over the same list API.
    launch_env: LaunchEnv = Field(default_factory=dict, exclude=True)
    source_mode: SessionSource = SessionSource.MANAGED
    # Set by the CLI from ``WAYPOINT_SESSION_ID`` when an agent inside a session
    # spawns this one. When ``permission_mode`` is unset and the spawner shares
    # this backend, the child inherits the spawner's mode.
    spawner_session_id: str | None = None
    worktree_path: str | None = None
    permission_mode: str | None = None
    model: str | None = None
    effort: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    # Apply a session preset before validation: ``preset_id`` names a specific
    # preset; ``use_default_preset`` applies the deployment default. Explicit
    # request fields always win over preset values. Resolution happens at the
    # request boundary (API / in-process CLI); the runtime stays preset-agnostic.
    preset_id: str | None = None
    use_default_preset: bool = False
    # Launch under a named account/config-dir profile. Resolved at launch: the
    # profile owns its config-dir env key (it wins over any raw launch_env value
    # for that key). Unknown ids are rejected. Label is derived server-side.
    account_profile_id: str | None = None


class SessionLaunchRequest(SessionCreateRequest):
    # Boundary input for POST /api/sessions and the in-process CLI launch:
    # ``backend`` may be omitted when a preset (or the default) supplies it.
    # ``cwd`` is never a preset field, so it stays effectively required — an
    # omitted cwd fails re-validation with a clean 400. Both are optional here
    # only so the resolver can merge and re-validate rather than 422 up-front.
    # Widening the parent's required fields to optional is an intentional Pydantic
    # subclass override; mypy flags it as an LSP violation, which does not apply
    # here (this type is only ever an input, never used where the parent is).
    backend: BackendId | None = None  # type: ignore[assignment]
    cwd: str | None = None  # type: ignore[assignment]


class SessionTagsUpdateRequest(BaseModel):
    # Merge ``set`` into the session's tags and drop each key in ``unset``.
    set: dict[str, str] = Field(default_factory=dict)
    unset: list[str] = Field(default_factory=list)


class SessionAttachRequest(BaseModel):
    tmux_target: str
    backend_hint: BackendId | None = None
    title: str | None = None


class SessionInputRequest(BaseModel):
    text: str
    submit: bool = True
    command: SessionCommandInvocation | None = None
    items: list[SessionInputItem] | None = None
    # Ids of previously uploaded attachments to deliver alongside the text.
    # Resolved to host paths server-side; see ``AttachmentSpec``.
    attachments: list[str] | None = None


class SessionApprovalRequest(BaseModel):
    decision: str
    text: str | None = None
    approval_id: str | None = None


class SessionPlanApprovalRequest(BaseModel):
    plan_item_id: str
    decision: Literal["accept", "acceptForSession", "decline", "cancel"] = "accept"
    text: str | None = None


ViewerId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]


class SessionPresenceRequest(BaseModel):
    # Opaque client-generated per-tab identifier; never a user identity.
    viewer_id: ViewerId


class SessionTitleRequest(BaseModel):
    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ]


class SessionPermissionModeRequest(BaseModel):
    mode: str


class SessionModelRequest(BaseModel):
    model: str | None = None


class SessionEffortRequest(BaseModel):
    effort: str | None = None


class LaunchSettingsUpdateRequest(BaseModel):
    """PATCH body for a session's restart-applied launch settings.

    Omitted ``args``/``config_overrides`` are left unchanged; when present they
    replace. ``env_set``/``env_unset`` patch the private launch env. When
    ``account_profile_id`` is set it owns the config-dir env key (any value for
    that key in ``env_set`` is dropped). ``restart`` must be true for a running
    session in phase 1.
    """

    # When set and different from the session's current transport, the session
    # is restarted onto the selected interface, keeping its native thread. Must
    # be a transport the session's agent declares and the server projects as a
    # safe target; the runtime rejects anything else.
    transport: SessionTransportId | None = None
    account_profile_id: str | None = None
    args: list[str] | None = None
    config_overrides: list[str] | None = None
    env_set: dict[str, str] = Field(default_factory=dict)
    env_unset: list[str] = Field(default_factory=list)
    restart: bool = False


class TransportSettingsOption(BaseModel):
    """A transport the session may switch to, with the restart-scoped launch
    capabilities of the resulting (agent, transport) pair.

    The server is authoritative: the modal populates its Interface selector from
    this list and reads the selected option's flags while a switch is staged,
    using the live backend catalog only for labels/presentation.
    """

    id: SessionTransportId
    supports_launch_settings_with_restart: bool = False
    supports_account_profile_with_restart: bool = False
    supports_custom_args: bool = False
    supports_config_overrides: bool = False


class LaunchSettingsResponse(BaseModel):
    """GET view of a session's restart-applied launch settings (redacted env)."""

    backend: BackendId
    transport: SessionTransportId
    launch_target_id: str | None = None
    account_profile_id: str | None = None
    account_profile_label: str | None = None
    account_profiles: list[AccountProfileMeta] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)
    config_overrides: list[str] = Field(default_factory=list)
    launch_env_keys: list[str] = Field(default_factory=list)
    # Keys the client must never edit or remove: runtime-owned
    # (WAYPOINT_SESSION_ID) plus the profile-owned config-dir key when a
    # profile is currently selected. Metadata only — the runtime validates
    # every patch regardless of what a client hides.
    protected_launch_env_keys: list[str] = Field(default_factory=list)
    # The agent's config-dir env var (CLAUDE_CONFIG_DIR / CODEX_HOME), or None.
    # Lets the client hide the profile-owned key for whichever profile is
    # currently selected or staged, without hard-coding the name.
    config_dir_env_var: str | None = None
    supports_custom_args: bool = False
    supports_config_overrides: bool = False
    supports_account_profile_with_restart: bool = False
    # True only when the session's (agent, transport) can restart-and-resume
    # AND Waypoint owns the process (i.e. not a bare attached tmux pane).
    supports_launch_settings_with_restart: bool = False
    # Interfaces this session may switch to (includes the current transport
    # first, then every safe target). Empty when switching isn't offered
    # (attached tmux, OpenCode, single-usable-transport agents, unpersisted
    # thread). The frontend must not infer switchability from the catalog alone.
    transport_options: list[TransportSettingsOption] = Field(default_factory=list)
    supports_transport_switch_with_restart: bool = False
    requires_restart: bool = True


class BackendModelOption(BaseModel):
    id: str
    label: str
    description: str | None = None
    is_default: bool = False
    hidden: bool = False
    # Reasoning-effort levels this model accepts (three-state):
    #   None -> efforts unknown; any level is accepted and forwarded unvalidated
    #   []   -> no effort knob (e.g. Claude Haiku); any effort is rejected
    #   list -> the accepted set; membership is enforced
    supported_efforts: list[str] | None = None
    default_effort: str | None = None


class BackendModelListResponse(BaseModel):
    backend: BackendId
    models: list[BackendModelOption] = Field(default_factory=list)
    # The literal model ID to use in requests.
    default_model_id: str | None = None
    # The human-friendly label for the default model.
    default_model_label: str | None = None
    supports_free_text: bool = False
    # Backend-wide default effort (used when no model-specific default
    # applies); falls back to None to mean "let the runtime pick".
    default_effort: str | None = None
    # The agent's full effort vocabulary; the frontend offers it as the effort
    # options for a selected model whose `supported_efforts` is None.
    effort_levels: list[str] = Field(default_factory=list)


class AskQuestionAnswer(BaseModel):
    question: str
    answer: str | None = None
    notes: str | None = None


class SessionAnswerQuestionRequest(BaseModel):
    answer: str
    tool_use_id: str | None = None
    answers: list[AskQuestionAnswer] | None = None


class ScheduleStatus(StrEnum):
    PENDING = "pending"
    LAUNCHED = "launched"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ScheduledSessionRecord(BaseModel):
    id: str
    backend: BackendId
    cwd: str
    launch_target_id: str | None = None
    launch_mode: LaunchMode = LaunchMode.AUTO
    transport: SessionTransportId | None = None
    title: str | None = None
    args: list[str] = Field(default_factory=list)
    config_overrides: list[str] = Field(default_factory=list)
    launch_env: LaunchEnv = Field(default_factory=dict, exclude=True)
    initial_prompt: str | None = None
    permission_mode: str | None = None
    model: str | None = None
    effort: str | None = None
    scheduled_at: datetime
    created_at: datetime
    status: ScheduleStatus = ScheduleStatus.PENDING
    session_id: str | None = None
    failure_reason: str | None = None
    # Recurrence: ``cron`` (five-field) + ``timezone`` (IANA) mark a recurring
    # definition; both null means one-time. ``scheduled_at`` is always the next
    # run. ``last_run_*`` carry the most recent claimed occurrence's outcome.
    cron: str | None = None
    timezone: str | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    last_failure_reason: str | None = None
    # Preset provenance snapshotted at schedule-creation time (see SessionRecord).
    preset_id: str | None = None
    preset_name: str | None = None
    # Account/config-dir profile snapshotted at schedule-creation time; the
    # schedule fires under this profile (see SessionRecord).
    account_profile_id: str | None = None
    account_profile_label: str | None = None


class ScheduleCreateRequest(BaseModel):
    backend: BackendId
    cwd: str
    launch_target_id: str | None = None
    launch_mode: LaunchMode = LaunchMode.AUTO
    # Pins the transport the scheduled session is driven over; carried into the
    # ``SessionCreateRequest`` the schedule fires so it reuses the create path's
    # (agent, transport) dispatch. ``None`` keeps the ``launch_mode`` behavior.
    transport: SessionTransportId | None = None
    title: str | None = None
    args: list[str] = Field(default_factory=list)
    config_overrides: list[str] = Field(default_factory=list)
    launch_env: LaunchEnv = Field(default_factory=dict)
    initial_prompt: str | None = None
    permission_mode: str | None = None
    model: str | None = None
    effort: str | None = None
    delay_seconds: int | None = None
    scheduled_at: datetime | None = None
    # Recurring timing: five-field cron + IANA timezone. ``start_at`` is the
    # recurrence's not-before wall-clock in that timezone (the first run is the
    # first cron occurrence at or after it); null starts now.
    cron: str | None = None
    timezone: str | None = None
    start_at: str | None = None
    # See SessionCreateRequest — apply a preset before validation. Explicit
    # request fields win; schedule timing/prompt fields are never taken from a
    # preset.
    preset_id: str | None = None
    use_default_preset: bool = False
    # Launch under a named account/config-dir profile (see SessionCreateRequest).
    account_profile_id: str | None = None


class ScheduleLaunchRequest(ScheduleCreateRequest):
    # Boundary input for POST /api/schedules — see SessionLaunchRequest.
    backend: BackendId | None = None  # type: ignore[assignment]
    cwd: str | None = None  # type: ignore[assignment]


class SchedulePreviewRequest(BaseModel):
    cron: str
    timezone: str
    start_at: str | None = None
    count: int = 3


class SchedulePreviewResponse(BaseModel):
    occurrences: list[datetime]


class SessionEnvelope(BaseModel):
    type: str
    payload: Mapping[str, Any]


class ScheduledMessageStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScheduledMessageRecord(BaseModel):
    id: str
    session_id: str
    text: str = ""
    submit: bool = True
    command: SessionCommandInvocation | None = None
    items: list[SessionInputItem] | None = None
    attachments: list[str] = Field(default_factory=list)
    scheduled_at: datetime
    created_at: datetime
    status: ScheduledMessageStatus = ScheduledMessageStatus.PENDING
    failure_reason: str | None = None
    # Recurrence — see ScheduledSessionRecord.
    cron: str | None = None
    timezone: str | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    last_failure_reason: str | None = None


class ScheduledMessageCreateRequest(BaseModel):
    text: str = ""
    submit: bool = True
    command: SessionCommandInvocation | None = None
    items: list[SessionInputItem] | None = None
    attachments: list[str] = Field(default_factory=list)
    delay_seconds: int | None = None
    scheduled_at: datetime | None = None
    # Recurring timing — see ScheduleCreateRequest.
    cron: str | None = None
    timezone: str | None = None
    start_at: str | None = None


class SideQuestionStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    ERROR = "error"


class SideQuestion(BaseModel):
    """An ephemeral ``/btw`` side-question on a Claude session.

    Answered from the current conversation with no tools, never written to the
    transcript, session-scoped. The same shape is the durable record persisted
    under ``SessionRecord.transport_state["pending_side_questions"]`` and the
    body of the ``side_question`` broadcast envelope.

    ``fork_thread_id`` is the forked Claude thread retained while the card is
    open so the aside can be promoted into a real session; it is ``None`` once
    the thread has been cleaned up. ``resumed`` is set when a pending aside was
    re-issued by the post-restart recovery sweep.
    """

    id: str
    question: str
    status: SideQuestionStatus
    answer: str | None = None
    error: str | None = None
    fork_thread_id: str | None = None
    attempts: int = 1
    resumed: bool = False
    created_at: datetime


# ─────────────────────────────── Inbox ───────────────────────────────
# A durable, human-facing inbox: a session (typically a crew lead) posts an
# item whose ordered ``blocks`` each carry an optional universal ``reply`` and,
# for interactive blocks, a structured ``answer``. The human triages in the UI;
# the requesting session reads the answers back as data. See
# ``Draft_Lead-Initiated_Human_Checkpoint_Inbox`` (RFC).


class InboxBlockType(StrEnum):
    MARKDOWN = "markdown"
    ATTACHMENT = "attachment"
    QUESTION = "question"
    APPROVAL = "approval"


class InboxStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class InboxAttachmentRef(BaseModel):
    # References a blob in an existing session's attachment store. Display
    # ``attachment`` blocks point at the requester's store; reply uploads land
    # in the requester session (pinned) and are stored the same way. The
    # runtime denormalizes ``filename``/``kind`` from the resolved spec at
    # write time (post/submit) so the UI renders the name inline without a
    # per-session lookup; both are None for an unresolvable ref.
    session_id: str
    attachment_id: str
    filename: str | None = None
    kind: AttachmentKind | None = None


class InboxQuestionOption(BaseModel):
    label: str
    description: str | None = None


class InboxQuestionAnswer(BaseModel):
    # Mirrors the session ``AskUserQuestion`` answer: selected option labels plus
    # optional free-text. ``extra="forbid"`` so an approval-shaped payload can
    # never silently validate as a question answer at the block-submit boundary.
    model_config = ConfigDict(extra="forbid")

    selected: list[str] = Field(default_factory=list)
    other: str | None = None


class InboxApprovalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str


class InboxReply(BaseModel):
    # Universal per-block comment: available on every block type, including
    # plain prose, so the human can annotate and attach files anywhere.
    notes: str | None = None
    attachments: list[InboxAttachmentRef] = Field(default_factory=list)
    created_at: datetime


class InboxReplyInput(BaseModel):
    notes: str | None = None
    attachments: list[InboxAttachmentRef] = Field(default_factory=list)


class _InboxBlockCommon(BaseModel):
    id: str
    reply: InboxReply | None = None


class InboxMarkdownBlock(_InboxBlockCommon):
    type: Literal[InboxBlockType.MARKDOWN] = InboxBlockType.MARKDOWN
    text: str


class InboxAttachmentBlock(_InboxBlockCommon):
    type: Literal[InboxBlockType.ATTACHMENT] = InboxBlockType.ATTACHMENT
    ref: InboxAttachmentRef


class InboxQuestionBlock(_InboxBlockCommon):
    type: Literal[InboxBlockType.QUESTION] = InboxBlockType.QUESTION
    header: str | None = None
    question: str
    options: list[InboxQuestionOption] = Field(default_factory=list)
    multi: bool = False
    required: bool = True
    answer: InboxQuestionAnswer | None = None
    answered_at: datetime | None = None


class InboxApprovalBlock(_InboxBlockCommon):
    type: Literal[InboxBlockType.APPROVAL] = InboxBlockType.APPROVAL
    prompt: str
    options: list[str] = Field(default_factory=list)
    required: bool = True
    answer: InboxApprovalAnswer | None = None
    answered_at: datetime | None = None


InboxBlock = Annotated[
    InboxMarkdownBlock | InboxAttachmentBlock | InboxQuestionBlock | InboxApprovalBlock,
    Field(discriminator="type"),
]

# The interactive block types that can gate resolution.
INBOX_INTERACTIVE_BLOCK_TYPES = frozenset(
    {InboxBlockType.QUESTION, InboxBlockType.APPROVAL}
)


class InboxItem(BaseModel):
    id: str
    # The session that posted the item, stamped from ``WAYPOINT_SESSION_ID``.
    from_session_id: str
    # Snapshot of the sender session's title at post time; a search target that
    # stays readable after the session row is gone.
    from_label: str | None = None
    subject: str
    status: InboxStatus = InboxStatus.OPEN
    # Null = unread. Independent of ``status``.
    read_at: datetime | None = None
    # Monotonic; bumped on every answer/reply and on a no-action resolve-on-read
    # (not on a plain read). The cursor for ``inbox wait --until update``.
    version: int = 0
    created_at: datetime
    updated_at: datetime
    blocks: list[InboxBlock] = Field(default_factory=list)


# ── Authoring (post) input: blocks without server-assigned ids/answers ──


class InboxMarkdownBlockInput(BaseModel):
    type: Literal[InboxBlockType.MARKDOWN] = InboxBlockType.MARKDOWN
    text: str


class InboxAttachmentBlockInput(BaseModel):
    type: Literal[InboxBlockType.ATTACHMENT] = InboxBlockType.ATTACHMENT
    ref: InboxAttachmentRef


class InboxQuestionBlockInput(BaseModel):
    type: Literal[InboxBlockType.QUESTION] = InboxBlockType.QUESTION
    header: str | None = None
    question: str
    options: list[InboxQuestionOption] = Field(default_factory=list)
    multi: bool = False
    required: bool = True


class InboxApprovalBlockInput(BaseModel):
    type: Literal[InboxBlockType.APPROVAL] = InboxBlockType.APPROVAL
    prompt: str
    options: list[str] = Field(default_factory=list)
    required: bool = True


InboxBlockInput = Annotated[
    InboxMarkdownBlockInput
    | InboxAttachmentBlockInput
    | InboxQuestionBlockInput
    | InboxApprovalBlockInput,
    Field(discriminator="type"),
]


class InboxPostRequest(BaseModel):
    subject: str
    blocks: list[InboxBlockInput] = Field(default_factory=list)
    from_session_id: str | None = None


class InboxBlockSubmitRequest(BaseModel):
    # A single UI action resolves a block: pick an option (``answer``), type a
    # note and/or attach files (``reply``) — either or both, in one submit. An
    # omitted field leaves the existing value untouched (never clears it).
    # ``answer`` is validated against the target block's type server-side.
    answer: dict[str, Any] | None = None
    reply: InboxReplyInput | None = None
    # The session performing the submit, so a wake subscriber is not woken by its
    # own mutation. ``None`` for human/UI answers (which do wake a subscriber).
    actor_session_id: str | None = None


class InboxListResponse(BaseModel):
    items: list[InboxItem] = Field(default_factory=list)
    has_more: bool = False
    cursor: str | None = None


class InboxUnresolvedCountResponse(BaseModel):
    unresolved_count: int


class InboxBatchDeleteRequest(BaseModel):
    item_ids: list[str] = Field(default_factory=list)


class InboxBatchDeleteResponse(BaseModel):
    # The ids that actually existed and were removed (unknown ids are ignored).
    deleted_ids: list[str] = Field(default_factory=list)
    count: int
