from waypoint.backends.codex.plugin import CodexPlugin
from waypoint.backends.codex.remote import (
    build_codex_launch_args,
    build_remote_codex_client_factory,
)
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.runtime import SessionRuntime
from waypoint.settings import Settings
from waypoint.storage import Storage


def test_remote_client_factory_uses_default_cwd_when_not_provided(
    monkeypatch,
) -> None:
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        default_cwd="~/workspace",
    )

    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    client = build_remote_codex_client_factory(config)("~/workspace", lambda *_: {})

    assert client.config.launch_args_override is not None
    # `~` must reach the remote shell unquoted so it can be expanded.
    assert "cd ~/workspace" in client.config.launch_args_override[2]


def test_remote_client_factory_uses_ssh_launch_args(monkeypatch) -> None:
    monkeypatch.setattr(
        "waypoint.launch_targets.shutil.which", lambda _: "/usr/bin/ssh"
    )
    config = SshLaunchTargetConfig(
        id="devbox",
        name="Devbox",
        ssh_destination="dev@example.com",
        ssh_args=["-p", "2222"],
        remote_env={"OPENAI_API_KEY": "sk-test"},
    )

    # Yaml→argv merging now happens in the plugin layer; the remote helper
    # just renders whatever lists it's given. Caller passes the effective
    # cli_args + config_overrides explicitly.
    client = build_remote_codex_client_factory(
        config,
        cli_args=("--verbose",),
        config_overrides=('model="gpt-5"',),
    )("/srv/work/project-a", lambda *_: {})

    assert client.config.launch_args_override is not None
    assert client.config.cwd is None
    assert client.config.launch_args_override[:4] == (
        "/usr/bin/ssh",
        "-p",
        "2222",
        "dev@example.com",
    )
    remote_command = client.config.launch_args_override[4]
    assert "cd /srv/work/project-a" in remote_command
    assert "app-server --listen stdio://" in remote_command
    # Raw cli flag reaches the remote shell unwrapped.
    assert "--verbose" in remote_command
    # K=V overrides are wrapped with --config.
    assert (
        "--config 'model=\"gpt-5\"'" in remote_command
        or 'model="gpt-5"' in remote_command
    )
    # Raw flag must precede `app-server` so codex sees it as a top-level
    # option, not as a subcommand argument.
    assert remote_command.index("--verbose") < remote_command.index("app-server")
    assert "OPENAI_API_KEY=sk-test" in remote_command


def test_build_codex_launch_args_orders_raw_flags_before_app_server() -> None:
    """Raw cli_args precede `--config` pairs and both precede `app-server`.

    Codex parses everything between `codex` and the subcommand as top-level
    options; flags placed after `app-server` would be passed to the
    subcommand instead and silently change behavior.
    """
    target = SshLaunchTargetConfig(
        id="local",
        name="Local",
        ssh_destination="dev@example.com",
        plugin_configs={"codex": {"remote_bin": "/opt/codex/codex"}},
        default_cwd="~/workspace",
    )

    args = build_codex_launch_args(
        target,
        "/srv/work",
        cli_args=("--verbose", "--no-color"),
        config_overrides=('model="gpt-5"', 'model_reasoning_effort="high"'),
    )

    rendered = " ".join(args)
    # Raw cli flags appear right after the codex binary, in input order.
    assert "/opt/codex/codex --verbose --no-color" in rendered
    # K=V overrides each get their own --config flag (the bash wrapper
    # adds extra quoting around the K=V value, so we just check the
    # value substring).
    assert rendered.count("--config") == 2
    # Every codex top-level option (raw flag, --config K=V) must appear
    # before `app-server` so codex parses them as top-level options
    # rather than positional args to the subcommand.
    app_server_idx = rendered.index("app-server")
    for token in ("--verbose", "--no-color", 'model="gpt-5"'):
        assert rendered.index(token) < app_server_idx, token


def test_codex_plugin_effective_args_merges_target_yaml_with_session(tmp_path) -> None:
    """`_effective_args` and `_effective_config_overrides` concatenate
    yaml-target lists with per-session lists in that order, for both
    `cli_args` and `config_overrides` independently.
    """
    settings = Settings(
        data_dir=tmp_path / "data",
        ssh_targets=[
            SshLaunchTargetConfig(
                id="devbox",
                name="Devbox",
                ssh_destination="dev@example.com",
                plugin_configs={
                    "codex": {
                        "cli_args": ["--verbose"],
                        "config_overrides": ['model="gpt-5"'],
                    }
                },
                default_cwd="~/workspace",
            )
        ],
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    plugin = runtime.registry.get("codex")
    assert isinstance(plugin, CodexPlugin)

    effective_cli = plugin._effective_args(runtime, "devbox", ["--debug"])
    effective_overrides = plugin._effective_config_overrides(
        runtime, "devbox", ['model_reasoning_effort="high"']
    )

    assert effective_cli == ["--verbose", "--debug"]
    assert effective_overrides == [
        'model="gpt-5"',
        'model_reasoning_effort="high"',
    ]


def test_codex_plugin_effective_args_uses_global_when_no_target(tmp_path) -> None:
    """With no launch target, `_effective_args` falls back to the global
    `plugin_configs.codex` block; config_overrides does the same.
    """
    settings = Settings(
        data_dir=tmp_path / "data",
        plugin_configs={
            "codex": {
                "cli_args": ["--verbose"],
                "config_overrides": ['model="gpt-5"'],
            }
        },
    )
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    plugin = runtime.registry.get("codex")
    assert isinstance(plugin, CodexPlugin)

    assert plugin._effective_args(runtime, None, ["--debug"]) == [
        "--verbose",
        "--debug",
    ]
    assert plugin._effective_config_overrides(runtime, None, ['x="y"']) == [
        'model="gpt-5"',
        'x="y"',
    ]
