import shlex
import shutil
from pathlib import Path

from codex_app_server.client import AppServerClient, AppServerConfig
from pydantic import BaseModel, Field


class RemoteCodexSshConfig(BaseModel):
    enabled: bool = False
    ssh_destination: str
    ssh_bin: str = "ssh"
    ssh_args: list[str] = Field(default_factory=list)
    codex_bin: str = "codex"
    default_remote_cwd: str = "~"
    config_overrides: list[str] = Field(default_factory=list)
    remote_env: dict[str, str] = Field(default_factory=dict)

    def build_launch_args(self, remote_cwd: str) -> tuple[str, ...]:
        ssh_bin = _resolve_local_binary(self.ssh_bin)
        codex_args = [self.codex_bin]
        for override in self.config_overrides:
            codex_args.extend(["--config", override])
        codex_args.extend(["app-server", "--listen", "stdio://"])

        remote_parts = [f"cd {shlex.quote(remote_cwd)}", "&&", "exec"]
        if self.remote_env:
            remote_parts.append("env")
            for key, value in sorted(self.remote_env.items()):
                remote_parts.append(shlex.quote(f"{key}={value}"))
        remote_parts.append(shlex.join(codex_args))
        remote_command = " ".join(remote_parts)
        return (ssh_bin, *self.ssh_args, self.ssh_destination, remote_command)


def build_remote_codex_client_factory(remote: RemoteCodexSshConfig):
    def factory(cwd: str, remote_cwd: str | None, approval_handler):
        launch_cwd = remote_cwd or remote.default_remote_cwd
        return AppServerClient(
            config=AppServerConfig(
                launch_args_override=remote.build_launch_args(launch_cwd),
                client_name="waypoint",
                client_title="Waypoint",
            ),
            approval_handler=approval_handler,
        )

    return factory


def _resolve_local_binary(binary: str) -> str:
    if "/" in binary:
        candidate = Path(binary).expanduser()
        if candidate.exists():
            return str(candidate)
        raise FileNotFoundError(f"binary not found: {candidate}")
    resolved = shutil.which(binary)
    if resolved is None:
        raise FileNotFoundError(f"binary not found on PATH: {binary}")
    return resolved
