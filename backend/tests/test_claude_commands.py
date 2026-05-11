import json

import pytest

from waypoint.backends.claude_code.commands import list_claude_command_completions


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
