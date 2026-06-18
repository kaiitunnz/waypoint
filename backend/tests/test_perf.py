import logging

from waypoint.perf import debug_timer


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _capture(logger: logging.Logger) -> list[logging.LogRecord]:
    handler = _ListHandler()
    logger.addHandler(handler)
    return handler.records


def test_debug_timer_emits_one_record_when_debug_enabled() -> None:
    logger = logging.getLogger("waypoint.test.perf.enabled")
    logger.setLevel(logging.DEBUG)
    records = _capture(logger)
    try:
        with debug_timer(logger, "do_thing", session="abc"):
            pass
    finally:
        logger.handlers.clear()
    assert len(records) == 1
    message = records[0].getMessage()
    assert "do_thing" in message
    assert "session" in message


def test_debug_timer_is_silent_when_debug_disabled() -> None:
    logger = logging.getLogger("waypoint.test.perf.disabled")
    logger.setLevel(logging.INFO)
    records = _capture(logger)
    try:
        with debug_timer(logger, "do_thing"):
            pass
    finally:
        logger.handlers.clear()
    assert records == []
