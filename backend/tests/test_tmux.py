import asyncio

from waypoint.tmux import TmuxAdapter


def test_send_input_uses_literal_mode_and_submit() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.send_input("%1", "hello world", submit=True))

    assert commands == [
        ("send-keys", "-t", "%1", "-l", "--", "hello world"),
        ("send-keys", "-t", "%1", "Enter"),
    ]


def test_send_input_preserves_multiline_text() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.send_input("%2", "first line\nsecond line", submit=True))

    assert commands == [
        ("send-keys", "-t", "%2", "-l", "--", "first line"),
        ("send-keys", "-t", "%2", "Enter"),
        ("send-keys", "-t", "%2", "-l", "--", "second line"),
        ("send-keys", "-t", "%2", "Enter"),
    ]
