"""Per-plugin configuration parsed from ``waypoint.yaml``.

Each backend plugin declares its own :class:`PluginConfig` subclass via
``BackendPlugin.config_schema``. ``Settings.plugin_configs`` is a flat
mapping ``plugin_id -> PluginConfig`` populated by the registry-aware
validator on ``Settings``; missing entries fall back to the schema's
defaults so plugin-specific YAML is always optional.
"""

from pydantic import BaseModel, ConfigDict


class PluginConfig(BaseModel):
    """Base class for per-plugin config blocks.

    Carries fields the runtime consumes uniformly across plugins
    (``default_model`` / ``default_effort``); plugin-specific fields
    (e.g. Claude's static model catalogue) live on the subclass.
    """

    model_config = ConfigDict(extra="forbid")

    default_model: str | None = None
    default_effort: str | None = None
