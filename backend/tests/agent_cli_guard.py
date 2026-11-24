"""Refuse to start a real coding-agent CLI from a backend test.

A real ``codex`` run against a fresh ``CODEX_HOME`` installs and detaches a
managed app-server daemon that outlives pytest, and a real ``claude``
or ``opencode`` run reaches the host's own accounts. The guard wraps
``subprocess.Popen``, which ``subprocess.run`` and
``asyncio.create_subprocess_exec`` both build on. Fake agent binaries that
tests write under a temp dir stay allowed. A command handed to a tmux server
runs outside this process and is not seen.
"""

import os
import shutil
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import pytest

AGENT_CLIS = frozenset({"claude", "codex", "opencode"})


def real_agent_cli(
    args: Any, kwargs: dict[str, Any], allowed_roots: Sequence[Path]
) -> str | None:
    """The resolved agent CLI a ``Popen(args, **kwargs)`` would start, if real."""
    if isinstance(args, (str, bytes, os.PathLike)):
        program = args
    elif args:
        program = args[0]
    else:
        return None
    name = os.fsdecode(program)
    if os.path.basename(name) not in AGENT_CLIS:
        return None
    if os.sep in name:
        cwd = kwargs.get("cwd")
        resolved: str | None = os.path.join(os.fsdecode(cwd), name) if cwd else name
    else:
        search_path = os.pathsep.join(os.get_exec_path(kwargs.get("env")))
        resolved = shutil.which(name, path=search_path)
    if resolved is None or not os.path.exists(resolved):
        return None
    real = Path(resolved).resolve()
    if any(real.is_relative_to(root) for root in allowed_roots):
        return None
    return str(real)


class AgentCliGuard:
    """Blocks and records every real agent CLI a test tries to start."""

    def __init__(self, allowed_roots: Iterable[Path]) -> None:
        self.allowed_roots = [Path(root).resolve() for root in allowed_roots]
        self.spawned: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = subprocess.Popen.__init__

        def guarded(
            popen: subprocess.Popen[Any], args: Any, *a: Any, **kw: Any
        ) -> None:
            if (binary := real_agent_cli(args, kw, self.allowed_roots)) is not None:
                self.spawned.append(binary)
                raise RuntimeError(
                    f"backend tests must not start a real agent CLI: {binary}"
                )
            original(popen, args, *a, **kw)

        monkeypatch.setattr(subprocess.Popen, "__init__", guarded)
