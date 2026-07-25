"""Claude Code model catalogue.

Static mirror of the per-model factory functions baked into the CLI
binary; bumped manually when a new alias ships. Codex has a runtime
``model/list`` RPC, Claude does not.
"""

from collections.abc import Callable, Sequence

from waypoint.schemas import BackendModelOption

# The effort vocabulary. Per-model `supported_efforts` below is the ladder Waypoint
# *offers and enforces*, not a mirror of what the CLI refuses: the binary carries
# per-capability lists (`effort`, `xhigh_effort`, `max_effort`) that drive its own
# picker, but the flag itself is accepted regardless -- `claude --model haiku --effort
# max` runs. So a narrower list here only ever narrows Waypoint. The server can also
# clamp a request at runtime via the account's `max_effort_level` entitlement.
CLAUDE_EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# Claude's CLI only exposes a small fixed catalog of aliases. The adapter may
# see either the human-facing alias (``opus[1m]``) or a resolved API model id
# (``claude-opus-4-8``); normalize both to the same family so the context window
# lookup stays stable.
#
# Append-only: entries must never be removed, even after a concrete model id
# is superseded. Resumed sessions persist historical concrete ids (e.g.
# claude-sonnet-4-5), and those ids must keep resolving for display and usage
# tracking for as long as such sessions can be resumed.
CLAUDE_MODEL_ALIASES: dict[str, str] = {
    "claude-opus-5": "opus",
    "claude-opus-4-8": "opus",
    "claude-opus-4-7": "opus",
    "claude-opus-4-6": "opus",
    "claude-opus-4-5": "opus",
    "claude-sonnet-5": "sonnet",
    "claude-sonnet-4-6": "sonnet",
    "claude-sonnet-4-5": "sonnet",
    "claude-haiku-4-5": "haiku",
    "claude-fable-5": "fable",
}

CLAUDE_CONTEXT_WINDOWS: dict[str, int] = {
    "opus": 200_000,
    "sonnet": 200_000,
    "haiku": 200_000,
    "fable": 200_000,
    "opus[1m]": 1_000_000,
    "sonnet[1m]": 1_000_000,
    "fable[1m]": 1_000_000,
}

# Pinned legacy models reachable on the current CLI by full model name (the CLI's own
# short picker keys -- `opus48`, `sonnet46`, ... -- are internal and rejected by
# `--model`). Reachability was probed against the 2.1.220 binary: `claude-opus-4-1` is
# silently remapped to Opus 5 and `claude-3-5-haiku` is retired, so neither is offered.
#
# `supported_efforts` is deliberately None (unknown -> forward unvalidated) rather than
# a per-model ladder. The binary's capability lists do imply narrower ladders for these,
# but they are not CLI-level rejection -- `claude --model haiku --effort max` is accepted
# -- so pinning a narrower list here would make *Waypoint* reject launches the CLI
# honors, including when only a configured `default_effort` supplies the level.
_LEGACY_CLAUDE_MODELS: tuple[BackendModelOption, ...] = (
    BackendModelOption(
        id="claude-opus-4-8",
        label="Opus 4.8",
        description="Previous Opus version",
    ),
    BackendModelOption(
        id="claude-opus-4-8[1m]",
        label="Opus 4.8 (1M context)",
        description="Previous Opus version, long sessions",
    ),
    BackendModelOption(
        id="claude-opus-4-7",
        label="Opus 4.7",
        description="Legacy Opus version",
    ),
    BackendModelOption(
        id="claude-opus-4-7[1m]",
        label="Opus 4.7 (1M context)",
        description="Legacy Opus version, long sessions",
    ),
    BackendModelOption(
        id="claude-opus-4-6",
        label="Opus 4.6",
        description="Legacy Opus version",
    ),
    BackendModelOption(
        id="claude-opus-4-6[1m]",
        label="Opus 4.6 (1M context)",
        description="Legacy Opus version, long sessions",
    ),
    # No [1m] pairing: claude-opus-4-5 is on the CLI's own 1M blocklist, and requesting
    # the suffix fails with "the long context beta is not yet available".
    BackendModelOption(
        id="claude-opus-4-5",
        label="Opus 4.5",
        description="Legacy Opus version",
    ),
    BackendModelOption(
        id="claude-sonnet-4-6",
        label="Sonnet 4.6",
        description="Previous Sonnet version",
    ),
    BackendModelOption(
        id="claude-sonnet-4-6[1m]",
        label="Sonnet 4.6 (1M context)",
        description="Previous Sonnet version, long sessions",
    ),
    BackendModelOption(
        id="claude-sonnet-4-5",
        label="Sonnet 4.5",
        description="Legacy Sonnet version",
    ),
    BackendModelOption(
        id="claude-sonnet-4-5[1m]",
        label="Sonnet 4.5 (1M context)",
        description="Legacy Sonnet version, long sessions",
    ),
)

# Grouped by family, newest first, each model immediately followed by its 1M variant.
DEFAULT_CLAUDE_MODELS: tuple[BackendModelOption, ...] = (
    BackendModelOption(
        id="opus",
        label="Opus 5",
        description="Most capable for complex work",
        supported_efforts=list(CLAUDE_EFFORT_LEVELS),
        default_effort="high",
    ),
    BackendModelOption(
        id="opus[1m]",
        label="Opus 5 (1M context)",
        description="Long sessions with large codebases",
        is_default=True,
        supported_efforts=list(CLAUDE_EFFORT_LEVELS),
        default_effort="high",
    ),
    BackendModelOption(
        id="sonnet",
        label="Sonnet 5",
        description="Best for everyday tasks",
        supported_efforts=list(CLAUDE_EFFORT_LEVELS),
        default_effort="high",
    ),
    BackendModelOption(
        id="sonnet[1m]",
        label="Sonnet 5 (1M context)",
        description="Long sessions with large codebases",
        supported_efforts=list(CLAUDE_EFFORT_LEVELS),
        default_effort="high",
    ),
    BackendModelOption(
        id="fable",
        label="Fable 5",
        description="Most capable for the hardest, longest-running tasks",
        supported_efforts=list(CLAUDE_EFFORT_LEVELS),
        default_effort="high",
    ),
    BackendModelOption(
        id="fable[1m]",
        label="Fable 5 (1M context)",
        description="Longest sessions with very large codebases",
        supported_efforts=list(CLAUDE_EFFORT_LEVELS),
        default_effort="high",
    ),
    BackendModelOption(
        id="haiku",
        label="Haiku 4.5",
        description="Fast and lightweight",
        # No effort knob; explicit [] so the None default (unknown) still rejects.
        supported_efforts=[],
    ),
    *_LEGACY_CLAUDE_MODELS,
)


_BUILTIN_MODEL_IDS: frozenset[str] = frozenset(opt.id for opt in DEFAULT_CLAUDE_MODELS)


def merge_model_catalogue(
    base: Sequence[BackendModelOption],
    extra: list[BackendModelOption],
) -> list[BackendModelOption]:
    """Append ``extra`` to ``base``.

    An extra whose id matches a base entry replaces that entry in place
    (preserving position), which lets an operator relabel or re-effort a
    built-in. Net-new extras append in declared order.
    """
    merged = list(base)
    index_by_id = {opt.id: i for i, opt in enumerate(merged)}
    for opt in extra:
        existing = index_by_id.get(opt.id)
        if existing is None:
            index_by_id[opt.id] = len(merged)
            merged.append(opt)
        else:
            merged[existing] = opt
    return merged


def overridden_builtin_ids(extra: list[BackendModelOption]) -> list[str]:
    """The ``extra`` ids that shadow a current-epoch built-in.

    The reference set is ``DEFAULT_CLAUDE_MODELS`` (startup-time; not gated on a
    target's CLI version).
    """
    return [opt.id for opt in extra if opt.id in _BUILTIN_MODEL_IDS]


_ONE_M_SUFFIX = "[1m]"


def _resolve_claude_model_base(candidate: str) -> str:
    normalized = CLAUDE_MODEL_ALIASES.get(candidate)
    if normalized is not None:
        return normalized
    if candidate.startswith("claude-opus-"):
        return "opus"
    if candidate.startswith("claude-sonnet-"):
        return "sonnet"
    if candidate.startswith("claude-haiku-"):
        return "haiku"
    if candidate.startswith("claude-fable-"):
        return "fable"
    # Backward-compat safety net: any historical or unknown model id that
    # merely mentions a family name still resolves to that family, so
    # resumed sessions with concrete ids we've never seen keep working.
    lowered = candidate.lower()
    for family in ("opus", "sonnet", "haiku", "fable"):
        if family in lowered:
            return family
    return candidate


def normalize_claude_model_id(model: str | None) -> str | None:
    if model is None or not isinstance(model, str):
        return None
    candidate = model.strip()
    if not candidate:
        return None
    if candidate in CLAUDE_CONTEXT_WINDOWS:
        return candidate
    # A concrete id may carry the 1M entitlement as a suffix
    # (``claude-opus-5[1m]``). Resolve the base family, then re-attach the
    # suffix so the entitlement survives normalization instead of collapsing to
    # the bare 200K family id. Families with no 1M offering (haiku) have no
    # ``[1m]`` catalogue entry, so the suffix is correctly dropped for them. An
    # id whose base resolves to nothing known keeps passing through untouched.
    if candidate.endswith(_ONE_M_SUFFIX):
        base = _resolve_claude_model_base(candidate[: -len(_ONE_M_SUFFIX)])
        if base not in CLAUDE_CONTEXT_WINDOWS:
            return candidate
        with_suffix = f"{base}{_ONE_M_SUFFIX}"
        return with_suffix if with_suffix in CLAUDE_CONTEXT_WINDOWS else base
    return _resolve_claude_model_base(candidate)


def claude_model_family(model: str | None) -> str | None:
    normalized = normalize_claude_model_id(model)
    if normalized is None:
        return None
    return normalized.split("[", 1)[0]


def claude_context_window_for_model(model: str | None) -> int | None:
    # normalize_claude_model_id resolves anything family-ish to a catalogue id
    # (including re-attaching a `[1m]` entitlement) and otherwise returns the
    # input verbatim, so an unresolved id has no family to fall back on --
    # don't fabricate a window for it.
    normalized = normalize_claude_model_id(model)
    if normalized is None:
        return None
    return CLAUDE_CONTEXT_WINDOWS.get(normalized)


def claude_default_model_id() -> str | None:
    for option in DEFAULT_CLAUDE_MODELS:
        if option.is_default:
            return option.id
    return None


def resolve_import_model_id(
    requested: str | None, default_model_id: str | None
) -> str | None:
    """Effective durable model for an imported thread.

    A non-blank request value wins (accepting both catalogue aliases and
    free-form ids, mirroring ``set_model``'s trim contract); otherwise the
    plugin's configured default. Returns ``None`` only when neither is set.
    """
    if isinstance(requested, str) and requested.strip():
        return requested.strip()
    return default_model_id


# CLI version milestones, from the official changelog (anthropics/claude-code):
#   2.1.154  Opus 4.8 introduced (defaults high; accepts xhigh + max)
#   2.1.170  Fable 5 introduced (accepts xhigh + max; 1M context)
#   2.1.197  Sonnet 5 introduced as the `sonnet` alias (native 1M context)
#   2.1.219  Opus 5 introduced as the `opus` alias (native 1M context)
#
# Each boundary below swaps an alias's target model, so only the affected
# family's labels/efforts differ across it. The rollbacks are applied
# cumulatively (newest first) rather than each rebuilding from
# DEFAULT_CLAUDE_MODELS, so an older CLI sees *every* rollback that applies to
# it, not just the newest one.
SONNET5_MIN_CLI_VERSION: tuple[int, ...] = (2, 1, 197)
OPUS5_MIN_CLI_VERSION: tuple[int, ...] = (2, 1, 219)

# Labels pre-Opus-5 CLI builds use for the opus ids.
_LEGACY_OPUS_LABELS: dict[str, str] = {
    "opus": "Opus 4.8",
    "opus[1m]": "Opus 4.8 (1M context)",
}

# Labels pre-Sonnet-5 CLI builds use for the sonnet ids.
_LEGACY_SONNET_LABELS: dict[str, str] = {
    "sonnet": "Sonnet 4.6",
    "sonnet[1m]": "Sonnet 4.6 (1M context)",
}

# Pinned ids each rollback makes redundant: once the alias itself resolves to Opus 4.8,
# the pinned claude-opus-4-8 entry is the same model under the same label, so the
# offering would list it twice. Dropping the pin (rather than the alias) keeps the
# default selection -- which is an alias id -- valid on every epoch.
_OPUS5_REDUNDANT_IDS: frozenset[str] = frozenset(
    {"claude-opus-4-8", "claude-opus-4-8[1m]"}
)
_SONNET5_REDUNDANT_IDS: frozenset[str] = frozenset(
    {"claude-sonnet-4-6", "claude-sonnet-4-6[1m]"}
)


def _roll_back_opus5(
    offering: tuple[BackendModelOption, ...],
) -> tuple[BackendModelOption, ...]:
    """``offering`` as CLI builds older than OPUS5_MIN_CLI_VERSION see it.

    On these builds the ``opus`` alias resolves to Opus 4.8, which accepts the
    same full effort set as Opus 5 (verified against the 2.1.218 and 2.1.220
    binaries: neither ``claude-opus-4-8`` nor ``claude-opus-5`` appears in the
    ``effort`` / ``xhigh_effort`` / ``max_effort`` exclusion lists), so only
    the label differs across this boundary. The pinned ``claude-opus-4-8`` entries drop
    out, since the rolled-back alias already offers that model under that label.
    """
    return tuple(
        (
            option.model_copy(update={"label": _LEGACY_OPUS_LABELS[option.id]})
            if option.id in _LEGACY_OPUS_LABELS
            else option
        )
        for option in offering
        if option.id not in _OPUS5_REDUNDANT_IDS
    )


def _roll_back_sonnet5(
    offering: tuple[BackendModelOption, ...],
) -> tuple[BackendModelOption, ...]:
    """``offering`` as CLI builds older than SONNET5_MIN_CLI_VERSION see it.

    On these builds the ``sonnet`` alias resolves to Sonnet 4.6, which accepts
    ``max`` but not ``xhigh`` (verified in the 2.1.175 / 2.1.195 / 2.1.196
    binaries: Sonnet 4.6's capabilities carry ``max_effort`` but not
    ``xhigh_effort``). Fable 5 and Haiku are identical across this boundary, so
    only the sonnet family is transformed, and the pinned ``claude-sonnet-4-6`` entries
    drop out as redundant with the rolled-back alias. Applied via ``model_copy`` to
    whatever offering the newer epochs produced, so unrelated catalogue edits
    (wording, descriptions, new fields) stay in sync automatically.
    """
    sonnet_efforts = [level for level in CLAUDE_EFFORT_LEVELS if level != "xhigh"]
    rolled: list[BackendModelOption] = []
    for option in offering:
        if option.id in _SONNET5_REDUNDANT_IDS:
            continue
        if option.id.split("[", 1)[0] != "sonnet":
            rolled.append(option)
            continue
        rolled.append(
            option.model_copy(
                update={
                    "label": _LEGACY_SONNET_LABELS.get(option.id, option.label),
                    "supported_efforts": sonnet_efforts,
                }
            )
        )
    return tuple(rolled)


# Each entry pairs the version a model epoch started at with the rollback that
# undoes it, i.e. the transform a build *older* than that version needs.
# Ordered newest first, and applied cumulatively, so introducing a future epoch
# is exactly one edit: prepend its ``(min_version, rollback)`` pair.
_CLAUDE_MODEL_EPOCH_ROLLBACKS: tuple[
    tuple[
        tuple[int, ...],
        Callable[[tuple[BackendModelOption, ...]], tuple[BackendModelOption, ...]],
    ],
    ...,
] = (
    (OPUS5_MIN_CLI_VERSION, _roll_back_opus5),
    (SONNET5_MIN_CLI_VERSION, _roll_back_sonnet5),
)


def claude_models_for_version(
    version: tuple[int, ...] | None,
) -> tuple[BackendModelOption, ...]:
    """The model catalogue to offer a CLI at ``version``.

    ``version=None`` means detection failed (remote launch target, missing
    binary, unparsable output, ...) -- callers should treat that as "assume
    latest" and get the current catalogue.
    """
    if version is None:
        return DEFAULT_CLAUDE_MODELS
    offering = DEFAULT_CLAUDE_MODELS
    for min_version, roll_back in _CLAUDE_MODEL_EPOCH_ROLLBACKS:
        if version >= min_version:
            break
        offering = roll_back(offering)
    return offering
