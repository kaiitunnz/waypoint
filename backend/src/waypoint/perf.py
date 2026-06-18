"""Low-overhead timing helpers for hot paths.

``debug_timer`` is a context manager that records how long a block took and
emits a single DEBUG log line. When the target logger has DEBUG disabled it
short-circuits before reading the clock or formatting anything, so the common
(non-DEBUG) case on streaming paths pays effectively nothing.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


@contextmanager
def debug_timer(logger: logging.Logger, label: str, **fields: Any) -> Iterator[None]:
    if not logger.isEnabledFor(logging.DEBUG):
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if fields:
            logger.debug("%s took %.2fms %r", label, elapsed_ms, fields)
        else:
            logger.debug("%s took %.2fms", label, elapsed_ms)
