"""Codex-specific helpers for launching the App Server over SSH.

The generic SSH primitives (``build_remote_exec_args``,
``wrap_remote_command``) live on ``SshLaunchTargetConfig``; everything
codex-shaped — building the ``codex app-server --listen stdio://`` argv
and wrapping it in an ``AppServerClient`` factory — lives here so
``launch_targets.py`` stays plugin-agnostic.
"""

from codex_app_server.client import AppServerClient, AppServerConfig

from waypoint.backends.codex.adapter import ApprovalCallback, ClientFactory
from waypoint.launch_targets import SshLaunchTargetConfig

CODEX_PLUGIN_ID = "codex"
CODEX_DEFAULT_BIN = "codex"


def build_codex_launch_args(
    target: SshLaunchTargetConfig,
    cwd: str,
    cli_args: tuple[str, ...] = (),
    config_overrides: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Build the remote argv for ``codex app-server``.

    Yaml-derived per-target ``cli_args`` and ``config_overrides`` are merged
    by the plugin layer (``CodexPlugin._effective_args`` /
    ``_effective_config_overrides``) before reaching here, so this function
    just renders whatever lists it receives. Order: ``codex_bin`` → raw
    flags → ``--config K=V`` pairs → ``app-server --listen stdio://``.
    """
    # Lazy import to break the plugin → remote → plugin cycle; the
    # value is always a ``CodexLaunchTargetConfig`` instance because
    # the codex plugin registered itself with that
    # ``launch_target_schema``.
    from waypoint.backends.codex.plugin import CodexLaunchTargetConfig

    config = target.plugin_config(CODEX_PLUGIN_ID)
    assert isinstance(config, CodexLaunchTargetConfig)
    codex_bin = config.remote_bin or CODEX_DEFAULT_BIN
    codex_args = [codex_bin]
    codex_args.extend(cli_args)
    for override in config_overrides:
        codex_args.extend(["--config", override])
    codex_args.extend(["app-server", "--listen", "stdio://"])
    return target.build_remote_exec_args(codex_args, cwd)


def build_remote_codex_client_factory(
    target: SshLaunchTargetConfig,
    cli_args: tuple[str, ...] = (),
    config_overrides: tuple[str, ...] = (),
) -> ClientFactory:
    def factory(cwd: str, approval_handler: ApprovalCallback) -> AppServerClient:
        launch_cwd = cwd or target.default_cwd
        return AppServerClient(
            config=AppServerConfig(
                launch_args_override=build_codex_launch_args(
                    target, launch_cwd, cli_args, config_overrides
                ),
                client_name="waypoint",
                client_title="Waypoint",
            ),
            approval_handler=approval_handler,
        )

    return factory
