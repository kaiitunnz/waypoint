from pathlib import Path
import shlex
import shutil

from codex_app_server.client import AppServerClient, AppServerConfig
from pydantic import BaseModel, Field


class CwdMapping(BaseModel):
    local_prefix: str
    remote_prefix: str


class RemoteCodexSshConfig(BaseModel):
    enabled: bool = False
    ssh_destination: str
    ssh_bin: str = "ssh"
    ssh_args: list[str] = Field(default_factory=list)
    codex_bin: str = "codex"
    config_overrides: list[str] = Field(default_factory=list)
    remote_env: dict[str, str] = Field(default_factory=dict)
    cwd_mappings: list[CwdMapping] = Field(default_factory=list)

    def resolve_remote_cwd(self, cwd: str) -> str:
        normalized = _normalize_user_path(cwd)
        match: tuple[str, str] | None = None
        for mapping in self.cwd_mappings:
            local_prefix = _normalize_user_path(mapping.local_prefix)
            if not _is_path_prefix(local_prefix, normalized):
                continue
            remote_prefix = _normalize_user_path(mapping.remote_prefix)
            if match is None or len(local_prefix) > len(match[0]):
                match = (local_prefix, remote_prefix)
        if match is None:
            return normalized
        suffix = normalized[len(match[0]) :].lstrip("/")
        if not suffix:
            return match[1]
        return f"{match[1].rstrip('/')}/{suffix}"

    def build_launch_args(self, cwd: str) -> tuple[str, ...]:
        ssh_bin = _resolve_local_binary(self.ssh_bin)
        remote_cwd = self.resolve_remote_cwd(cwd)
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
    def factory(cwd: str, approval_handler):
        return AppServerClient(
            config=AppServerConfig(
                launch_args_override=remote.build_launch_args(cwd),
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


def _normalize_user_path(raw: str) -> str:
    value = Path(raw).expanduser().as_posix()
    if value != "/":
        value = value.rstrip("/")
    return value or "/"


def _is_path_prefix(prefix: str, value: str) -> bool:
    if prefix == value:
        return True
    if prefix == "/":
        return value.startswith("/")
    return value.startswith(f"{prefix}/")
