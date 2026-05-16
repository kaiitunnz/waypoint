"""Tests for the server-side terminal renderer used by the tmux WS endpoint."""

import re

from waypoint.backends.tmux.renderer import PyteRenderer, SyncFrameTracker


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)


def test_full_render_paints_each_row_with_cup() -> None:
    r = PyteRenderer(10, 3)
    r.feed(b"abc\r\ndef")
    out = r.render_full()
    # Each row gets a CUP at column 1, then content (no CSI K — the
    # initial \x1b[2J at frame start handles the clear, and cell-level
    # diffing emits per-cell writes from there).
    assert "\x1b[2J" in out
    assert "\x1b[1;1H" in out
    assert "\x1b[2;1H" in out
    assert _strip_ansi(out).startswith("abc")


def test_render_diff_only_emits_changed_cells() -> None:
    r = PyteRenderer(10, 3)
    r.feed(b"row1\r\nrow2")
    r.render_full()  # establishes the mirror
    # Write three new chars on row 3.
    r.feed(b"\x1b[3;1H!!!")
    diff = r.render_diff()
    # Only the three new cells should be emitted (CUP to (3,1), then "!!!").
    assert "\x1b[3;1H" in diff
    assert "!!!" in _strip_ansi(diff)
    # Untouched rows must NOT be repainted.
    assert "row1" not in _strip_ansi(diff)
    assert "row2" not in _strip_ansi(diff)


def test_render_diff_suppresses_noop_dirty_rows() -> None:
    """Codex re-clears textbox-border rows every frame even when their
    visible content doesn't change. Pyte still marks them dirty, but
    the mirror should make the diff a no-op."""
    r = PyteRenderer(20, 5)
    r.feed(b"\x1b[1;1Hborder line\x1b[2;1Hcontent")
    r.render_full()
    # Re-clear-and-rewrite the same content (what Codex does each frame).
    r.feed(b"\x1b[1;1H\x1b[Kborder line\x1b[2;1H\x1b[Kcontent")
    diff = r.render_diff()
    # Mirror equality should suppress emit entirely.
    assert diff == ""


def test_render_diff_emits_only_the_changed_cell() -> None:
    """One-char typing should produce a one-cell diff, not a row repaint."""
    r = PyteRenderer(20, 5)
    r.feed(b"\x1b[3;1Habc")
    r.render_full()
    # Now Codex's per-keystroke frame style: clear surrounding rows then
    # write one new char.
    r.feed(b"\x1b[3;1H\x1b[K\x1b[2;1H\x1b[K\x1b[3;1Habcd")
    diff = r.render_diff()
    text = _strip_ansi(diff)
    # Only the new 'd' should be in the emitted text.
    assert "d" in text
    assert "abc" not in text


def test_render_diff_empty_when_no_changes() -> None:
    r = PyteRenderer(10, 3)
    r.feed(b"hi")
    r.render_full()
    assert r.render_diff() == ""


def test_sgr_colors_translate_back_to_ansi() -> None:
    r = PyteRenderer(20, 2)
    r.feed(b"\x1b[31mred\x1b[1;32mgr\x1b[0m")
    out = r.render_full()
    assert ";31;" in out
    assert ";1;" in out  # bold
    assert ";32;" in out


def test_truecolor_emits_38_2_rgb() -> None:
    r = PyteRenderer(10, 2)
    r.feed(b"\x1b[38;2;100;150;200mx")
    out = r.render_full()
    assert "38;2;100;150;200" in out


def test_alt_screen_toggle_emits_buffer_switch() -> None:
    r = PyteRenderer(10, 3)
    r.feed(b"main")
    r.render_full()
    r.feed(b"\x1b[?1049h\x1b[2J\x1b[Halt")
    diff = r.render_diff()
    assert "\x1b[?1049h" in diff
    assert "alt" in _strip_ansi(diff)
    r.feed(b"\x1b[?1049l")
    back = r.render_diff()
    assert "\x1b[?1049l" in back


def test_cursor_position_and_visibility() -> None:
    r = PyteRenderer(10, 3)
    r.feed(b"\x1b[2;5Hx")
    out = r.render_full()
    # Cursor at 2,6 (after writing 'x' at col 5)
    assert "\x1b[2;6H" in out
    assert "\x1b[?25h" in out  # visible
    r.feed(b"\x1b[?25l")
    diff = r.render_diff()
    assert "\x1b[?25l" in diff


def test_set_cursor_seeds_position() -> None:
    r = PyteRenderer(10, 3)
    r.set_cursor(4, 1)
    out = r.render_full()
    assert "\x1b[2;5H" in out


def test_resize_forces_full_repaint_next_diff() -> None:
    r = PyteRenderer(10, 3)
    r.feed(b"hi")
    r.render_full()
    r.resize(20, 5)
    diff = r.render_diff()
    # Resize invalidates the mirror; the next diff must paint every
    # row in the new geometry from column 1.
    for row in range(1, 6):
        assert f"\x1b[{row};1H" in diff
    assert r.cols == 20 and r.rows == 5


def test_wide_chars_skip_empty_continuation_cell() -> None:
    r = PyteRenderer(10, 2)
    r.feed("a漢b".encode())
    out = r.render_full()
    # Strip ANSI; the rendered line should preserve all three glyphs in order.
    text = _strip_ansi(out)
    assert "a漢b" in text


def test_sync_output_markers_are_ignored() -> None:
    r = PyteRenderer(10, 2)
    r.feed(b"\x1b[?2026hA\x1b[2;5HB\x1b[?2026l")
    out = r.render_full()
    text = _strip_ansi(out)
    assert "A" in text and "B" in text


def test_private_csi_sgr_is_stripped_before_pyte_sees_it() -> None:
    """``\\x1b[>4;2m`` (kitty keyboard / modifyOtherKeys mode-set) is a
    private CSI — the ``>`` intermediate is supposed to mark it as
    device-specific. Pyte ignores the intermediate and treats ``4``/``2``
    as SGR params, latching ``underscore=True`` on every subsequent cell.
    claude_code emits this at startup and almost never sends a full
    ``\\x1b[0m`` reset (it toggles individual attrs), so without the
    filter every line of text renders underlined in xterm.
    """
    r = PyteRenderer(20, 2)
    r.feed(b"\x1b[>4;2mhello")
    out = r.render_full()
    # The emitted SGRs must not include the underline param 4 — if they
    # do, pyte latched underscore on from the private CSI.
    for sgr in re.findall(r"\x1b\[([0-9;]+)m", out):
        params = sgr.split(";")
        assert "4" not in params, f"underline leaked via private CSI: {sgr}"
    assert "hello" in _strip_ansi(out)


def test_private_csi_sgr_other_intermediates_also_stripped() -> None:
    # Same shape with ``<`` / ``?`` intermediates: both are private and
    # pyte mis-parses both. Belt-and-suspenders so future TUIs using
    # other private-prefix mode-sets don't regress us.
    for prefix in (b"<", b"?"):
        r = PyteRenderer(20, 2)
        r.feed(b"\x1b[" + prefix + b"4mhi")
        out = r.render_full()
        for sgr in re.findall(r"\x1b\[([0-9;]+)m", out):
            assert "4" not in sgr.split(
                ";"
            ), f"underline leaked via private CSI with prefix {prefix!r}"


def test_real_sgr_underline_still_works() -> None:
    # The filter must only strip private-CSI SGRs, never the regular
    # SGR form. Explicit ``\x1b[4m`` should still underline.
    r = PyteRenderer(20, 2)
    r.feed(b"\x1b[4munder\x1b[24m off")
    out = r.render_full()
    # At least one emitted SGR should carry the underline param.
    sgrs = re.findall(r"\x1b\[([0-9;]+)m", out)
    assert any("4" in s.split(";") for s in sgrs), out


def test_dec_private_modes_not_affected() -> None:
    # Alt-screen toggle uses the private intermediate ``?`` but ends in
    # ``h`` / ``l`` — must not be stripped (the filter is anchored on
    # the ``m`` final byte).
    r = PyteRenderer(10, 2)
    r.feed(b"main")
    r.render_full()
    r.feed(b"\x1b[?1049halt")
    out = r.render_diff()
    assert "\x1b[?1049h" in out


def test_private_csi_split_across_feeds_is_still_stripped() -> None:
    # The bootstrap ``\x1b[>4;2m`` could in principle arrive split if
    # pipe-pane reads land mid-sequence. The tail-buffer must hold
    # the partial CSI so the next feed can complete and strip it.
    r = PyteRenderer(20, 2)
    r.feed(b"\x1b[>4;2")  # no terminator yet
    r.feed(b"mhello")
    out = r.render_full()
    for sgr in re.findall(r"\x1b\[([0-9;]+)m", out):
        assert "4" not in sgr.split(";"), out
    assert "hello" in _strip_ansi(out)


def test_frame_tracker_detects_whole_markers() -> None:
    t = SyncFrameTracker()
    assert t.feed(b"hello") is False
    assert t.feed(b"\x1b[?2026h") is True
    assert t.feed(b"content") is True
    assert t.feed(b"\x1b[?2026l") is False


def test_frame_tracker_survives_marker_split_across_chunks() -> None:
    t = SyncFrameTracker()
    # Split the 8-byte open marker every which way.
    assert t.feed(b"\x1b[?2") is False
    assert t.feed(b"026h") is True
    assert t.feed(b"content") is True
    # And the close marker split too.
    assert t.feed(b"\x1b") is True
    assert t.feed(b"[?2026") is True
    assert t.feed(b"l") is False


def test_frame_tracker_multiple_frames_in_one_chunk() -> None:
    t = SyncFrameTracker()
    # Two complete frames in one chunk — ends outside any frame.
    assert t.feed(b"\x1b[?2026hAAA\x1b[?2026l\x1b[?2026hBBB\x1b[?2026l") is False
    # Frame opens but doesn't close.
    assert t.feed(b"\x1b[?2026hCCC") is True


def test_frame_tracker_ignores_non_2026_csi_sharing_prefix() -> None:
    t = SyncFrameTracker()
    # CSI ? 2026 m would share the prefix bytes — make sure the
    # state machine resets after a non-h/l terminator.
    assert t.feed(b"\x1b[?2026m") is False
    # Then a real open should still register.
    assert t.feed(b"\x1b[?2026h") is True


def test_frame_tracker_split_at_frame_ends_one_frame() -> None:
    t = SyncFrameTracker()
    segs = t.split_at_frame_ends(b"\x1b[?2026hAAA\x1b[?2026l")
    # One closed-frame segment covering the whole chunk.
    assert segs == [(b"\x1b[?2026hAAA\x1b[?2026l", True)]


def test_frame_tracker_split_at_frame_ends_multiple_frames() -> None:
    t = SyncFrameTracker()
    chunk = b"\x1b[?2026hAAA\x1b[?2026l\x1b[?2026hBBB\x1b[?2026l"
    segs = t.split_at_frame_ends(chunk)
    # Two closed-frame segments, recombining to the original.
    assert len(segs) == 2
    assert all(ended for _, ended in segs)
    assert b"".join(s for s, _ in segs) == chunk


def test_frame_tracker_split_with_trailing_open_frame() -> None:
    t = SyncFrameTracker()
    chunk = b"\x1b[?2026hAAA\x1b[?2026l\x1b[?2026hCCC"
    segs = t.split_at_frame_ends(chunk)
    # First seg closes, second seg is open (still in frame).
    assert len(segs) == 2
    assert segs[0][1] is True  # closed
    assert segs[1][1] is False  # still in frame
    assert t.in_frame is True


def test_frame_tracker_split_carries_state_across_calls() -> None:
    t = SyncFrameTracker()
    # First call: opens but doesn't close.
    segs1 = t.split_at_frame_ends(b"\x1b[?2026hAAA")
    assert segs1 == [(b"\x1b[?2026hAAA", False)]
    assert t.in_frame is True
    # Second call: closes — that whole segment should be a closed
    # frame even though it doesn't contain the open marker.
    segs2 = t.split_at_frame_ends(b"BBB\x1b[?2026l")
    assert segs2 == [(b"BBB\x1b[?2026l", True)]
    assert t.in_frame is False
