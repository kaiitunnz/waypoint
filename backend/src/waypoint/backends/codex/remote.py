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


def build_codex_launch_args(target: SshLaunchTargetConfig, cwd: str) -> tuple[str, ...]:
    codex_bin = (
        target.remote_bin_for(CODEX_PLUGIN_ID, CODEX_DEFAULT_BIN) or CODEX_DEFAULT_BIN
    )
    codex_args = [codex_bin]
    for override in target.config_overrides:
        codex_args.extend(["--config", override])
    codex_args.extend(["app-server", "--listen", "stdio://"])
    return target.build_remote_exec_args(codex_args, cwd)


def build_remote_codex_client_factory(target: SshLaunchTargetConfig) -> ClientFactory:
    def factory(cwd: str, approval_handler: ApprovalCallback) -> AppServerClient:
        launch_cwd = cwd or target.default_cwd
        return AppServerClient(
            config=AppServerConfig(
                launch_args_override=build_codex_launch_args(target, launch_cwd),
                client_name="waypoint",
                client_title="Waypoint",
            ),
            approval_handler=approval_handler,
        )

    return factory
