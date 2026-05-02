"""Per-plugin configuration parsed from ``waypoint.yaml``.

Two layers, both keyed on plugin id:

- :class:`PluginConfig` — global per-plugin config from
  ``Settings.plugin_configs`` (default model, default effort, plus
  plugin-specific fields like Claude's curated model catalogue).
- :class:`PluginLaunchTargetConfig` — per-target per-plugin config
  from ``SshLaunchTargetConfig.plugin_configs`` (remote binary path,
  plus plugin-specific fields like Codex's ``--config`` overrides).

Each plugin declares its own subclass for both layers via
``BackendPlugin.config_schema`` and ``BackendPlugin.launch_target_schema``;
the registry-aware validators on ``Settings`` and
``SshLaunchTargetConfig`` populate the typed instances eagerly so YAML
errors surface at startup rather than first runtime access.
"""

from pydantic import BaseModel, ConfigDict


class PluginConfig(BaseModel):
    """Base class for per-plugin global config blocks.

    Carries fields the runtime consumes uniformly across plugins
    (``default_model_id`` / ``default_effort``, ``local_bin``); plugin-specific
    fields (e.g. Claude's static model catalogue) live on the subclass.
    """

    model_config = ConfigDict(extra="forbid")

    default_model_id: str | None = None
    default_effort: str | None = None
    # Path or PATH-resolvable name of the plugin's CLI binary on the
    # local host. ``None`` means "fall back to the plugin's
    # ``capabilities.cli_binary``" so a default install Just Works.
    # The SSH-target counterpart is ``PluginLaunchTargetConfig.remote_bin``.
    local_bin: str | None = None


class PluginLaunchTargetConfig(BaseModel):
    """Base class for per-target per-plugin config blocks.

    Carries fields every plugin's per-target config can use uniformly
    (``remote_bin`` — the binary path on this SSH target). Plugin
    subclasses extend this with their own fields; Codex adds
    ``config_overrides`` for the ``--config K=V`` flag, for example.
    """

    model_config = ConfigDict(extra="forbid")

    # Path or PATH-resolvable name of the plugin's CLI binary on the
    # remote host. ``None`` means "fall back to the plugin's
    # ``capabilities.cli_binary``" so a default install Just Works.
    remote_bin: str | None = None
