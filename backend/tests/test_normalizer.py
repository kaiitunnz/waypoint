from waypoint.normalizer import TerminalNormalizer
from waypoint.schemas import EventKind, SessionStatus


def test_normalizer_detects_approval_request() -> None:
    normalizer = TerminalNormalizer()
    result = normalizer.normalize(
        "session-1",
        "Approve this command? y/n",
        1,
    )
    assert result.events[0].kind == EventKind.APPROVAL_REQUEST
    assert result.status == SessionStatus.WAITING_INPUT


def test_normalizer_strips_ansi_sequences() -> None:
    normalizer = TerminalNormalizer()
    cleaned = normalizer.clean("\x1b[31mhello\x1b[0m")
    assert cleaned == "hello"
