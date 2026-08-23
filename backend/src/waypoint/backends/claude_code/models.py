"""Claude Code model catalogue.

Static mirror of the per-model factory functions baked into the CLI
binary; bumped manually when a new alias ships. Codex has a runtime
``model/list`` RPC, Claude does not.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import NamedTuple

from waypoint.schemas import BackendModelOption

# Per-model `supported_efforts` is the ladder Waypoint offers and enforces. The CLI
# accepts `--effort` for every model, so a narrower list only narrows Waypoint; the
# server may still clamp a request via the account's `max_effort_level` entitlement.
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

# Legacy models the CLI still runs, pinned by full model name; its own short picker
# keys (`opus48`, `sonnet46`, ...) are internal and rejected by `--model`.
# `claude-opus-4-1` is remapped to Opus 5 and `claude-3-5-haiku` is retired, so neither
# is offered. `supported_efforts` stays unset, leaving the CLI the authority. Probed
# against 2.1.220; the remap/retirement table is server-fetched and can drift.
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
    # No [1m] pairing: claude-opus-4-5 is on the CLI's 1M blocklist.
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
        # Offer no effort control; explicit [] so the None default (unknown) rejects.
        # A deployment with `default_effort` set therefore cannot launch `haiku`
        # without overriding the effort -- see issue #361.
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
    # A concrete id may carry the 1M entitlement as a suffix (`claude-opus-5[1m]`);
    # re-attach it to the resolved base so the entitlement survives. A family with no
    # 1M offering (haiku) has no `[1m]` entry, so the suffix resolves away.
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
    # An unresolved id normalizes to itself, so it has no window rather than a
    # fabricated default.
    normalized = normalize_claude_model_id(model)
    if normalized is None:
        return None
    return CLAUDE_CONTEXT_WINDOWS.get(normalized)


# A pure "selected model id -> context window tokens" lookup. The static
# ``claude_context_window_for_model`` is itself a valid resolver and is the
# default every usage producer falls back to when no configured override applies.
ClaudeContextWindowResolver = Callable[[str | None], int | None]


def configured_context_windows(
    models: Iterable[BackendModelOption],
    extra_models: Iterable[BackendModelOption],
) -> dict[str, int]:
    """Map configured model ids to their operator-declared context window.

    Only entries carrying a non-null ``context_window`` contribute; the static
    built-in catalogue (windows unset) never does. ``extra_models`` is overlaid
    last so it wins a collision with the replacement-style ``models`` list, the
    same precedence ``merge_model_catalogue`` uses.
    """
    mapping: dict[str, int] = {}
    for option in (*models, *extra_models):
        if option.context_window is not None:
            mapping[option.id.strip()] = option.context_window
    return mapping


def resolve_claude_context_window(
    model: str | None, configured: Mapping[str, int]
) -> int | None:
    """Resolve ``model``'s window: exact configured id first, then static table.

    An exact configured id wins over built-in inference (letting an operator
    override a built-in through replacement semantics); otherwise the static
    resolver applies, and an id neither source knows resolves to ``None`` rather
    than a fabricated default.
    """
    if isinstance(model, str):
        window = configured.get(model.strip())
        if window is not None:
            return window
    return claude_context_window_for_model(model)


def make_context_window_resolver(
    models: Iterable[BackendModelOption],
    extra_models: Iterable[BackendModelOption],
) -> ClaudeContextWindowResolver:
    """A resolver bound to a configuration's declared custom windows."""
    configured = configured_context_windows(models, extra_models)
    if not configured:
        # No operator overrides -> the static resolver is exactly equivalent and
        # avoids an extra dict lookup on every usage update.
        return claude_context_window_for_model
    return lambda model: resolve_claude_context_window(model, configured)


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


# Catalogue boundaries from the official changelog (anthropics/claude-code). Only the
# affected family differs across each. The introduction boundaries carry no rollback:
# a CLI below them is still offered the model, so `fable` reaches builds older than
# 2.1.170.
#   2.1.154  Opus 4.8 introduced      (unhandled)
#   2.1.170  Fable 5 introduced       (unhandled)
#   2.1.197  `sonnet` becomes Sonnet 5
#   2.1.219  `opus` becomes Opus 5
SONNET5_MIN_CLI_VERSION: tuple[int, ...] = (2, 1, 197)
OPUS5_MIN_CLI_VERSION: tuple[int, ...] = (2, 1, 219)


class _ModelEpoch(NamedTuple):
    """What an alias swap looks like to CLI builds older than ``min_version``.

    ``labels`` relabels the affected alias ids and ``efforts`` narrows their ladder.
    ``drop`` removes the pinned ids the relabelled alias now duplicates; the pin goes
    rather than the alias so the default selection, an alias id, stays valid.
    """

    min_version: tuple[int, ...]
    labels: Mapping[str, str]
    drop: frozenset[str]
    efforts: tuple[str, ...] | None = None


# Newest first, applied cumulatively, so a build below several boundaries gets every
# rollback.
_CLAUDE_MODEL_EPOCHS: tuple[_ModelEpoch, ...] = (
    _ModelEpoch(
        min_version=OPUS5_MIN_CLI_VERSION,
        labels={"opus": "Opus 4.8", "opus[1m]": "Opus 4.8 (1M context)"},
        drop=frozenset({"claude-opus-4-8", "claude-opus-4-8[1m]"}),
        # No `efforts`: Opus 4.8 is in none of the capability lists, as Opus 5 is not.
    ),
    _ModelEpoch(
        min_version=SONNET5_MIN_CLI_VERSION,
        labels={"sonnet": "Sonnet 4.6", "sonnet[1m]": "Sonnet 4.6 (1M context)"},
        drop=frozenset({"claude-sonnet-4-6", "claude-sonnet-4-6[1m]"}),
        # Sonnet 4.6 is in `xhigh_effort` but not `max_effort` (2.1.196 binary).
        efforts=tuple(level for level in CLAUDE_EFFORT_LEVELS if level != "xhigh"),
    ),
)


def _roll_back(
    offering: tuple[BackendModelOption, ...], epoch: _ModelEpoch
) -> tuple[BackendModelOption, ...]:
    """``offering`` as builds older than ``epoch.min_version`` see it."""
    rolled: list[BackendModelOption] = []
    for option in offering:
        if option.id in epoch.drop:
            continue
        label = epoch.labels.get(option.id)
        if label is None:
            rolled.append(option)
            continue
        update: dict[str, str | list[str]] = {"label": label}
        if epoch.efforts is not None:
            update["supported_efforts"] = list(epoch.efforts)
        rolled.append(option.model_copy(update=update))
    return tuple(rolled)


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
    for epoch in _CLAUDE_MODEL_EPOCHS:
        if version >= epoch.min_version:
            break
        offering = _roll_back(offering, epoch)
    return offering
