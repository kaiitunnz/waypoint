"""Claude Code model catalogue.

Static mirror of the per-model factory functions baked into the CLI
binary; bumped manually when a new alias ships. Codex has a runtime
``model/list`` RPC, Claude does not.
"""

from waypoint.schemas import BackendModelOption

# Effort levels gated by the binary's per-model checks (`vy`/`L4_`/`k4_`):
# opus-4-6/4-7 and sonnet-4-6 expose the full set; haiku and older opus/sonnet
# don't accept --effort at all.
CLAUDE_EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh")

DEFAULT_CLAUDE_MODELS: tuple[BackendModelOption, ...] = (
    BackendModelOption(
        id="opus",
        label="Opus 4.7",
        description="Most capable for complex work",
        is_default=True,
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
        label="Opus 4.7 (1M context)",
        description="Long sessions with large codebases",
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
)
