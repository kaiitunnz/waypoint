"""Claude Code model catalogue.

Static mirror of the per-model factory functions baked into the CLI
binary; bumped manually when a new alias ships. Codex has a runtime
``model/list`` RPC, Claude does not.
"""

from waypoint.schemas import BackendModelOption

# Effort levels gated by the binary's per-model checks (`vy`/`L4_`/`k4_`):
# opus-4-6/4-7/4-8 and sonnet-4-6 expose the full set; haiku and older
# opus/sonnet don't accept --effort at all. Fable 5 adds `max`.
CLAUDE_EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh")
CLAUDE_FABLE_EFFORT_LEVELS: tuple[str, ...] = CLAUDE_EFFORT_LEVELS + ("max",)

# Claude's CLI only exposes a small fixed catalog of aliases. The adapter may
# see either the human-facing alias (``opus[1m]``) or a resolved API model id
# (``claude-opus-4-8``); normalize both to the same family so the context window
# lookup stays stable.
CLAUDE_MODEL_ALIASES: dict[str, str] = {
    "claude-opus-4-8": "opus",
    "claude-opus-4-7": "opus",
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
        label="Sonnet 4.6",
        description="Best for everyday tasks",
        supported_efforts=list(CLAUDE_EFFORT_LEVELS),
        default_effort="high",
    ),
    BackendModelOption(
        id="haiku",
        label="Haiku 4.5",
        description="Fast and lightweight",
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
        label="Sonnet 4.6 (1M context)",
        description="Long sessions with large codebases",
        supported_efforts=list(CLAUDE_EFFORT_LEVELS),
        default_effort="high",
    ),
    BackendModelOption(
        id="fable",
        label="Fable 5",
        description="Most capable for the hardest, longest-running tasks",
        supported_efforts=list(CLAUDE_FABLE_EFFORT_LEVELS),
        default_effort="high",
    ),
    BackendModelOption(
        id="fable[1m]",
        label="Fable 5 (1M context)",
        description="Longest sessions with very large codebases",
        supported_efforts=list(CLAUDE_FABLE_EFFORT_LEVELS),
        default_effort="high",
    ),
)


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
    if family is None:
        return None
    return CLAUDE_CONTEXT_WINDOWS.get(family)


def claude_default_model_id() -> str | None:
    for option in DEFAULT_CLAUDE_MODELS:
        if option.is_default:
            return option.id
    return None
