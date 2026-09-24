import json

import pytest

from waypoint.backends.claude_code.commands import list_claude_command_completions
from waypoint.launch_targets import SshLaunchTargetConfig


@pytest.mark.asyncio
async def test_list_claude_command_completions_reads_project_commands(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    command_dir = repo / ".claude" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "humanizer.md").write_text(
        "---\ndescription: Make the text sound natural\n---\nPrompt body\n",
        encoding="utf-8",
    )

    completions = await list_claude_command_completions(
        cwd=str(repo),
        claude_bin=str(tmp_path / "missing-claude"),
        prefix="/hum",
    )

    assert [item.name for item in completions] == ["humanizer"]
    assert completions[0].replacement == "/humanizer "
    assert completions[0].description == "Make the text sound natural"
    assert completions[0].source == "custom_command"


@pytest.mark.asyncio
async def test_list_claude_command_completions_reads_user_commands(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    command_dir = home / ".claude" / "commands" / "team"
    command_dir.mkdir(parents=True)
    (command_dir / "review.md").write_text(
        "---\ndescription: Team review checklist\n---\nPrompt body\n",
        encoding="utf-8",
    )

    completions = await list_claude_command_completions(
        cwd=str(tmp_path / "repo"),
        claude_bin=str(tmp_path / "missing-claude"),
        prefix="/team",
    )

    assert [item.name for item in completions] == ["team/review"]
    assert completions[0].replacement == "/team/review "


@pytest.mark.asyncio
async def test_list_claude_command_completions_scopes_to_config_dir(
    tmp_path, monkeypatch
) -> None:
    # A profile-scoped session's user commands live under its CLAUDE_CONFIG_DIR,
    # not the default ~/.claude; completion must scan the profile dir.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # empty default account
    profile = tmp_path / "profile"
    command_dir = profile / "commands" / "team"
    command_dir.mkdir(parents=True)
    (command_dir / "ship.md").write_text(
        "---\ndescription: Ship it\n---\nbody\n", encoding="utf-8"
    )

    default = await list_claude_command_completions(
        cwd=str(tmp_path / "repo"),
        claude_bin=str(tmp_path / "missing-claude"),
        prefix="/team",
    )
    scoped = await list_claude_command_completions(
        cwd=str(tmp_path / "repo"),
        claude_bin=str(tmp_path / "missing-claude"),
        prefix="/team",
        config_dir=str(profile),
    )
    assert [item.name for item in default] == []
    assert [item.name for item in scoped] == ["team/ship"]


@pytest.mark.asyncio
async def test_list_claude_command_completions_reads_plugin_skills(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    plugin_dir = tmp_path / "plugin"
    skill_dir = plugin_dir / "skills" / "frontend-design"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: frontend-design\ndescription: Design frontend UI\n---\n",
        encoding="utf-8",
    )
    claude = tmp_path / "claude"
    claude.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' {json.dumps(json.dumps([{'enabled': True, 'installPath': str(plugin_dir)}]))}\n",
        encoding="utf-8",
    )
    claude.chmod(0o755)

    completions = await list_claude_command_completions(
        cwd=str(tmp_path / "repo"),
        claude_bin=str(claude),
        prefix="/frontend",
    )

    assert [item.name for item in completions] == ["frontend-design"]
    assert completions[0].kind == "skill"
    assert completions[0].source == "plugin_skill"


@pytest.mark.asyncio
async def test_list_claude_command_completions_reads_user_skills(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    skill_dir = home / ".claude" / "skills" / "create-pr"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: create-pr\ndescription: Open a PR\nargument-hint: <branch>\n---\n",
        encoding="utf-8",
    )

    completions = await list_claude_command_completions(
        cwd=str(tmp_path / "repo"),
        claude_bin=str(tmp_path / "missing-claude"),
        prefix="/create",
    )

    assert [item.name for item in completions] == ["create-pr"]
    assert completions[0].kind == "skill"
    assert completions[0].source == "user_skill"
    assert completions[0].argument_hint == "<branch>"


@pytest.mark.asyncio
async def test_list_claude_command_completions_workspace_skill_overrides_home(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "repo"
    (home / ".claude" / "skills" / "shared").mkdir(parents=True)
    (home / ".claude" / "skills" / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: Home variant\n---\n",
        encoding="utf-8",
    )
    (repo / ".claude" / "skills" / "shared").mkdir(parents=True)
    (repo / ".claude" / "skills" / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: Workspace variant\n---\n",
        encoding="utf-8",
    )

    completions = await list_claude_command_completions(
        cwd=str(repo),
        claude_bin=str(tmp_path / "missing-claude"),
        prefix="/shared",
    )

    assert len(completions) == 1
    assert completions[0].description == "Workspace variant"


@pytest.mark.asyncio
async def test_list_claude_command_completions_propagates_command_argument_hint(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    command_dir = repo / ".claude" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "review.md").write_text(
        "---\ndescription: Review a branch\nargument-hint: <branch>\n---\n",
        encoding="utf-8",
    )

    completions = await list_claude_command_completions(
        cwd=str(repo),
        claude_bin=str(tmp_path / "missing-claude"),
        prefix="/rev",
    )

    assert completions[0].argument_hint == "<branch>"


def _loopback_remote(monkeypatch, home) -> list[dict[str, str] | None]:
    # Run the vendored remote discovery script on this host under a fake remote
    # HOME, recording the per-call env the SSH argv would have carried.
    captured: list[dict[str, str] | None] = []

    def _build(self, command, cwd=None, *, allocate_tty=False, extra_env=None):
        captured.append(extra_env)
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin", **(extra_env or {})}
        return ("env", "-i", *(f"{k}={v}" for k, v in env.items()), *command)

    monkeypatch.setattr(SshLaunchTargetConfig, "build_remote_exec_args", _build)
    return captured


def _write_skill(root, name: str) -> None:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\nBody\n", encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_remote_discovery_scopes_to_profile_config_dir(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    _write_skill(home / ".claude" / "skills", "default-only")
    _write_skill(home / ".claude-work" / "skills", "profile-only")
    captured = _loopback_remote(monkeypatch, home)

    completions = await list_claude_command_completions(
        cwd="~/repo",
        claude_bin="missing-claude",
        prefix="/",
        launch_target=SshLaunchTargetConfig(id="t", name="t", ssh_destination="d"),
        config_dir="~/.claude-work",
    )

    assert captured == [{"CLAUDE_CONFIG_DIR": "~/.claude-work"}]
    assert [item.name for item in completions] == ["profile-only"]


@pytest.mark.asyncio
async def test_remote_discovery_expands_tilde_cwd(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    _write_skill(home / "repo" / ".claude" / "skills", "project-skill")
    captured = _loopback_remote(monkeypatch, home)

    completions = await list_claude_command_completions(
        cwd="~/repo",
        claude_bin="missing-claude",
        prefix="/",
        launch_target=SshLaunchTargetConfig(id="t", name="t", ssh_destination="d"),
    )

    assert captured == [None]
    assert [item.name for item in completions] == ["project-skill"]


@pytest.mark.asyncio
async def test_remote_discovery_matches_local(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    skills = home / "repo" / ".claude" / "skills"
    bodies = {
        "plain": "name: plain\ndescription: Plain text\n",
        "quoted": 'name: quoted\ndescription: "Quoted: with colon"\n',
        "folded": "name: folded\ndescription: >-\n  Folded across\n  two lines\n",
        "literal": "name: literal\ndescription: |\n  Line one\n  Line two\nargument-hint: <x>\n",
    }
    for dirname, body in bodies.items():
        (skills / dirname).mkdir(parents=True)
        (skills / dirname / "SKILL.md").write_text(f"---\n{body}---\nBody\n")
    commands = home / ".claude" / "commands"
    commands.mkdir(parents=True)
    (commands / "hint.md").write_text("---\nargument-hint: >\n  [file]\n---\nBody\n")
    monkeypatch.setenv("HOME", str(home))

    local = await list_claude_command_completions(
        cwd=str(home / "repo"), claude_bin="missing-claude", prefix="/"
    )
    _loopback_remote(monkeypatch, home)
    remote = await list_claude_command_completions(
        cwd="~/repo",
        claude_bin="missing-claude",
        prefix="/",
        launch_target=SshLaunchTargetConfig(id="t", name="t", ssh_destination="d"),
    )

    assert len(local) == 5
    assert [item.model_dump() for item in remote] == [
        item.model_dump() for item in local
    ]
    by_name = {item.name: item for item in remote}
    assert by_name["folded"].description == "Folded across two lines"
    assert by_name["literal"].argument_hint == "<x>"
