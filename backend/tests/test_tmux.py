import asyncio

import pytest

from waypoint.backends.tmux.adapter import TmuxAdapter, TmuxError


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


def test_send_input_pastes_multiline_text_then_submits() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.send_input("%2", "first line\nsecond line", submit=True))

    # Multi-line text is delivered as a bracketed paste rather than a burst
    # of literal keystrokes + Ctrl-J: the C-j/Enter emulation intermittently
    # left the message typed-but-unsent against the wrapped TUI's line editor
    # (which has bracketed paste enabled). The paste carries the whole block;
    # the trailing Enter that ``submit=True`` adds submits it.
    set_buffer, paste_buffer, submit = commands
    assert set_buffer[:2] == ("set-buffer", "-b")
    buffer_name = set_buffer[2]
    assert buffer_name.startswith("waypoint-input-")
    assert set_buffer[3:] == ("--", "first line\nsecond line")
    # The paste targets the same buffer the set-buffer just wrote.
    assert paste_buffer == (
        "paste-buffer",
        "-d",
        "-p",
        "-r",
        "-b",
        buffer_name,
        "-t",
        "%2",
    )
    assert submit == ("send-keys", "-t", "%2", "Enter")


def test_send_input_pastes_carriage_returns_normalized_to_lf() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(
        adapter.send_input("%2", "msg\r\n\r\nAttached files:\r\n- /tmp/a", submit=True)
    )

    # CRLF/CR are normalized to LF before the buffer is set, so the paste
    # never carries a bare CR that the inner app would read as accept-line.
    set_buffer = commands[0]
    assert set_buffer[:2] == ("set-buffer", "-b")
    assert set_buffer[3:] == ("--", "msg\n\nAttached files:\n- /tmp/a")


def test_multiline_sends_use_distinct_buffer_names() -> None:
    names: list[str] = []

    async def fake_run(*args: str) -> str:
        if args[0] == "set-buffer":
            names.append(args[2])
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    # Input is not serialized per pane, so each send must use its own buffer
    # name or concurrent sends to the same pane would clobber each other.
    asyncio.run(adapter.send_input("%2", "a\nb", submit=True))
    asyncio.run(adapter.send_input("%2", "c\nd", submit=True))

    assert len(names) == 2
    assert names[0] != names[1]


def test_multiline_paste_failure_drops_orphaned_buffer() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        if args[0] == "paste-buffer":
            raise TmuxError("pane gone")
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    with pytest.raises(TmuxError):
        asyncio.run(adapter.send_input("%2", "a\nb", submit=True))

    # paste-buffer -d never deleted the buffer (it failed), so a best-effort
    # delete-buffer runs for the same buffer before the error propagates.
    set_buffer, paste_buffer, delete_buffer = commands
    buffer_name = set_buffer[2]
    assert paste_buffer[0] == "paste-buffer"
    assert delete_buffer == ("delete-buffer", "-b", buffer_name)


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


def test_resize_window_pins_manual_size_and_disables_status() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.resize_window("waypoint-abc", 120, 40))

    assert commands == [
        ("set-option", "-t", "waypoint-abc", "window-size", "manual"),
        ("set-option", "-t", "waypoint-abc", "status", "off"),
        ("resize-window", "-t", "waypoint-abc", "-x", "120", "-y", "40"),
    ]


def test_resize_pane_targets_pane_id() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.resize_pane("%4", 120, 40))

    assert commands == [
        ("resize-pane", "-t", "%4", "-x", "120", "-y", "40"),
    ]


def test_pane_screen_state_parses_alt_and_cursor() -> None:
    async def fake_run(*args: str) -> str:
        assert args == (
            "display-message",
            "-p",
            "-t",
            "%4",
            "#{alternate_on}|#{cursor_x}|#{cursor_y}",
        )
        # tmux reports 0-based coordinates; the helper returns 1-based.
        return "1|7|29\n"

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    alt, col, row = asyncio.run(adapter.pane_screen_state("%4"))
    assert alt is True
    assert (col, row) == (8, 30)


def test_pane_screen_state_handles_normal_screen() -> None:
    async def fake_run(*args: str) -> str:
        return "0|0|0"

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    alt, col, row = asyncio.run(adapter.pane_screen_state("%4"))
    assert alt is False
    assert (col, row) == (1, 1)


def test_describe_target_parses_live_pane() -> None:
    async def fake_run(*args: str) -> str:
        return "sess|0|%7|/home/u|0|4242\n"

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    target = asyncio.run(adapter.describe_target("%7"))
    assert target.pane == "%7"
    assert target.pane_dead is False
    assert target.pane_pid == 4242


def test_describe_target_raises_on_missing_pane() -> None:
    # `display-message -t` does not validate the target: a missing pane expands
    # to all-empty fields with exit 0 ("|||||"). describe_target must treat that
    # as a dead target so liveness checks mark the session exited instead of
    # seeing pane_dead=False on a phantom pane.
    async def fake_run(*args: str) -> str:
        return "|||||"

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    with pytest.raises(TmuxError):
        asyncio.run(adapter.describe_target("%77"))


def test_target_exists_false_for_missing_pane() -> None:
    async def fake_run(*args: str) -> str:
        return "|||||"

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    assert asyncio.run(adapter.target_exists("%77")) is False
