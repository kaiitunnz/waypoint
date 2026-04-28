import shlex
import shutil
from pathlib import Path

from codex_app_server.client import AppServerClient, AppServerConfig
from pydantic import BaseModel, Field, field_validator

from waypoint.schemas import Backend


def _default_supported_backends() -> list[Backend]:
    return [Backend.CODEX, Backend.CLAUDE_CODE]


class SshLaunchTargetConfig(BaseModel):
    id: str
    name: str
    enabled: bool = True
    ssh_destination: str
    ssh_bin: str = "ssh"
    ssh_args: list[str] = Field(default_factory=list)
    codex_bin: str = "codex"
    claude_bin: str = "claude"
    default_remote_cwd: str = "~"
    supported_backends: list[Backend] = Field(default_factory=_default_supported_backends)
    config_overrides: list[str] = Field(default_factory=list)
    remote_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("supported_backends")
    @classmethod
    def validate_supported_backends(cls, value: list[Backend]) -> list[Backend]:
        if not value:
            return _default_supported_backends()
        deduped: list[Backend] = []
        for backend in value:
            if backend not in deduped:
                deduped.append(backend)
        return deduped

    def supports(self, backend: Backend) -> bool:
        return backend in self.supported_backends

    def resolve_default_backend(self, fallback: Backend) -> Backend:
        if fallback in self.supported_backends:
            return fallback
        return self.supported_backends[0]

    def build_codex_launch_args(self, remote_cwd: str) -> tuple[str, ...]:
        codex_args = [self.codex_bin]
        for override in self.config_overrides:
            codex_args.extend(["--config", override])
        codex_args.extend(["app-server", "--listen", "stdio://"])
        return self.build_remote_exec_args(codex_args, remote_cwd)

    def build_remote_exec_args(self, command: list[str], remote_cwd: str) -> tuple[str, ...]:
        ssh_bin = _resolve_local_binary(self.ssh_bin)
        remote_parts = [f"cd {_quote_remote_path(remote_cwd)}", "&&", "exec"]
        if self.remote_env:
            remote_parts.append("env")
            for key, value in sorted(self.remote_env.items()):
                remote_parts.append(shlex.quote(f"{key}={value}"))
        remote_parts.append(shlex.join(command))
        remote_command = " ".join(remote_parts)
        return (ssh_bin, *self.ssh_args, self.ssh_destination, remote_command)

    def remote_command_for_backend(self, backend: Backend, args: list[str], remote_cwd: str) -> tuple[str, ...]:
        executable = self.claude_bin if backend == Backend.CLAUDE_CODE else self.codex_bin
        return self.build_remote_exec_args([executable, *args], remote_cwd)


def build_remote_codex_client_factory(target: SshLaunchTargetConfig):
    def factory(cwd: str, remote_cwd: str | None, approval_handler):
        launch_cwd = remote_cwd or target.default_remote_cwd
        return AppServerClient(
            config=AppServerConfig(
                launch_args_override=target.build_codex_launch_args(launch_cwd),
                client_name="waypoint",
                client_title="Waypoint",
            ),
            approval_handler=approval_handler,
        )

    return factory


def _quote_remote_path(path: str) -> str:
    """Quote a path for the remote shell while preserving tilde expansion.

    `shlex.quote` wraps the whole string in single quotes, which suppresses
    the remote shell's tilde substitution. For paths starting with `~` (e.g.
    `~`, `~/foo`, or `~user/bar`) leave the tilde-prefix unquoted so the
    remote shell can expand it, and quote only the suffix.
    """
    if not path or not path.startswith("~"):
        return shlex.quote(path)
    head, sep, rest = path.partition("/")
    if not sep:
        return head
    if not rest:
        return f"{head}/"
    return f"{head}/{shlex.quote(rest)}"


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
