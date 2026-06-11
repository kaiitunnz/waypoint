import subprocess
from pathlib import Path

import typer


def skills_helper_script(home: Path) -> Path:
    return home / "scripts" / "install_skills.sh"


def run_skills_helper(home: Path, command: str, extra: list[str]) -> None:
    script = skills_helper_script(home)
    if not script.is_file():
        typer.echo(f"skills helper not found: {script}", err=True)
        raise typer.Exit(code=1)
    argv = ["bash", str(script), command, *extra]
    completed = subprocess.run(argv, cwd=home, check=False)
    raise typer.Exit(code=completed.returncode)
