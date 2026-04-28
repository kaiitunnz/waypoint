import asyncio
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitMeta:
    repo_name: str | None
    branch: str | None


async def resolve_git_meta(cwd: str) -> GitMeta:
    cwd_path = Path(cwd).expanduser()
    if not cwd_path.exists():
        return GitMeta(repo_name=cwd_path.name or None, branch=None)
    toplevel = await _git(cwd_path, "rev-parse", "--show-toplevel")
    branch = await _git(cwd_path, "rev-parse", "--abbrev-ref", "HEAD")
    if toplevel is None:
        return GitMeta(repo_name=cwd_path.name or None, branch=None)
    repo_name = Path(toplevel).name or cwd_path.name or None
    return GitMeta(repo_name=repo_name, branch=branch)


async def _git(cwd: Path, *args: str) -> str | None:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return None
    return stdout.decode().strip() or None
