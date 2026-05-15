import asyncio

from waypoint.backends.tmux.adapter import TmuxAdapter


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


def test_send_bytes_forwards_hex_escape_sequences() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    # ESC [ A — up arrow.
    asyncio.run(adapter.send_bytes("%1", b"\x1b[A"))
    # Ctrl-C.
    asyncio.run(adapter.send_bytes("%1", b"\x03"))
    # UTF-8 multibyte (é).
    asyncio.run(adapter.send_bytes("%1", "é".encode()))

    assert commands == [
        ("send-keys", "-t", "%1", "-H", "1b", "5b", "41"),
        ("send-keys", "-t", "%1", "-H", "03"),
        ("send-keys", "-t", "%1", "-H", "c3", "a9"),
    ]


def test_send_bytes_skips_empty_payload() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.send_bytes("%1", b""))

    assert commands == []


def test_resize_window_emits_tmux_command() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.resize_window("waypoint-abc", 120, 40))

    assert commands == [
        ("resize-window", "-t", "waypoint-abc", "-x", "120", "-y", "40"),
    ]
