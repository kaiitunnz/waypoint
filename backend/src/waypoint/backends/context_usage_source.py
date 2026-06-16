from abc import ABC, abstractmethod


class ContextUsageSource(ABC):
    """Cancellable background task that publishes context usage for
    non-structured-transport sessions.

    The runtime wraps :meth:`run` in an ``asyncio.Task`` and cancels it
    when the session exits.  Concrete implementations poll an agent artifact
    and push updates via ``runtime.update_session_fields(context_usage=...)``.
    """

    @abstractmethod
    async def run(self) -> None: ...
