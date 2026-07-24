"""Claude Code model catalogue.

Static mirror of the per-model factory functions baked into the CLI
binary; bumped manually when a new alias ships. Codex has a runtime
``model/list`` RPC, Claude does not.
"""

from collections.abc import Callable, Sequence

from waypoint.schemas import BackendModelOption

# Effort levels the CLI accepts per model (verified against v2.1.197's
# gate functions: `Jw` for effort at all, `Rne` for xhigh, `c0e` for max).
# opus-4-8, sonnet-5, and fable-5 accept the full set; haiku and pre-4.6
# opus/sonnet don't accept --effort at all. The server can still clamp a
# request at runtime via the account's `max_effort_level` entitlement.
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
    "claude-opus-4-8": "opus",
    "claude-opus-4-7": "opus",
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

DEFAULT_CLAUDE_MODELS: tuple[BackendModelOption, ...] = (
    BackendModelOption(
        id="opus",
        label="Opus 4.8",
        description="Most capable for complex work",
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
        id="haiku",
        label="Haiku 4.5",
        description="Fast and lightweight",
        # No effort knob; explicit [] so the None default (unknown) still rejects.
        supported_efforts=[],
    ),
    BackendModelOption(
        id="opus[1m]",
        label="Opus 4.8 (1M context)",
        description="Long sessions with large codebases",
        is_default=True,
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


def normalize_claude_model_id(model: str | None) -> str | None:
    if model is None or not isinstance(model, str):
        return None
    candidate = model.strip()
    if not candidate:
        return None
    if candidate in CLAUDE_CONTEXT_WINDOWS:
        return candidate
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


def claude_model_family(model: str | None) -> str | None:
    normalized = normalize_claude_model_id(model)
    if normalized is None:
        return None
    return normalized.split("[", 1)[0]


def claude_context_window_for_model(model: str | None) -> int | None:
    normalized = normalize_claude_model_id(model)
    if normalized is None:
        return None
    if normalized in CLAUDE_CONTEXT_WINDOWS:
        return CLAUDE_CONTEXT_WINDOWS[normalized]
    family = claude_model_family(normalized)
    if family is None or family not in CLAUDE_CONTEXT_WINDOWS:
        # No family could be inferred at all (genuine garbage) -- don't
        # fabricate a window.
        return None
    candidate = model.strip() if isinstance(model, str) else ""
    if normalized.endswith("[1m]") or candidate.endswith("[1m]"):
        return 1_000_000
    return CLAUDE_CONTEXT_WINDOWS[family]


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
#
# 2.1.197 is the offering boundary we gate on: it swaps the `sonnet` alias from
# Sonnet 4.6 to Sonnet 5. Sonnet 5 accepts `xhigh` where Sonnet 4.6 did not
# (4.6 accepts `max` but not `xhigh`); Opus 4.8 and Fable 5 already accept the
# full set on both sides of the boundary. Verified against the 2.1.175 /
# 2.1.195 / 2.1.196 / 2.1.197 binaries installed on this host.
SONNET5_MIN_CLI_VERSION: tuple[int, ...] = (2, 1, 197)

# Labels pre-Sonnet-5 CLI builds use for the sonnet ids.
_LEGACY_SONNET_LABELS: dict[str, str] = {
    "sonnet": "Sonnet 4.6",
    "sonnet[1m]": "Sonnet 4.6 (1M context)",
}


def _pre_sonnet5_offering() -> tuple[BackendModelOption, ...]:
    """The catalogue offered by CLI builds older than SONNET5_MIN_CLI_VERSION.

    On these builds the ``sonnet`` alias resolves to Sonnet 4.6, which accepts
    ``max`` but not ``xhigh`` (verified in the 2.1.175 / 2.1.195 / 2.1.196
    binaries: Sonnet 4.6's capabilities carry ``max_effort`` but not
    ``xhigh_effort``). Opus 4.8, Fable 5, and Haiku are identical to the
    current catalogue across this boundary, so only the sonnet family is
    transformed. Derived from ``DEFAULT_CLAUDE_MODELS`` via ``model_copy`` so
    unrelated catalogue edits (wording, descriptions, new fields) stay in sync
    automatically.
    """
    sonnet_efforts = [level for level in CLAUDE_EFFORT_LEVELS if level != "xhigh"]
    offering: list[BackendModelOption] = []
    for option in DEFAULT_CLAUDE_MODELS:
        if option.id.split("[", 1)[0] != "sonnet":
            offering.append(option)
            continue
        offering.append(
            option.model_copy(
                update={
                    "label": _LEGACY_SONNET_LABELS.get(option.id, option.label),
                    "supported_efforts": sonnet_efforts,
                }
            )
        )
    return tuple(offering)


# Ordered by descending minimum version so adding a future epoch is a small,
# obvious edit: prepend a new ``(min_version, builder)`` pair at the front.
_CLAUDE_MODEL_EPOCHS: tuple[
    tuple[tuple[int, ...], Callable[[], tuple[BackendModelOption, ...]]], ...
] = (
    (SONNET5_MIN_CLI_VERSION, lambda: DEFAULT_CLAUDE_MODELS),
    ((0,), _pre_sonnet5_offering),
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
    for min_version, builder in _CLAUDE_MODEL_EPOCHS:
        if version >= min_version:
            return builder()
    return DEFAULT_CLAUDE_MODELS
