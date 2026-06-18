import asyncio
from datetime import UTC, datetime

from waypoint.backends.claude_code.rate_limits import SharedRateLimitProbeCache
from waypoint.schemas import SessionRateLimitUsage, UsageWindow


def _snapshot(used: float = 10.0) -> SessionRateLimitUsage:
    return SessionRateLimitUsage(
        source="claude_code",
        updated_at=datetime.now(UTC),
        windows=[
            UsageWindow(
                id="five_hour",
                label="5h",
                used_percent=used,
                window_minutes=300,
                resets_at=None,
            )
        ],
        notes=["CLI creds"],
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_serves_cached_snapshot_within_ttl() -> None:
    clock = _Clock()
    cache = SharedRateLimitProbeCache(ttl_seconds=300.0, clock=clock)
    calls = 0

    async def fetch() -> SessionRateLimitUsage:
        nonlocal calls
        calls += 1
        return _snapshot()

    first = await cache.get_or_probe("acct", fetch)
    clock.now = 299.0
    second = await cache.get_or_probe("acct", fetch)

    assert calls == 1
    assert first is second


async def test_refetches_after_ttl_expires() -> None:
    clock = _Clock()
    cache = SharedRateLimitProbeCache(ttl_seconds=300.0, clock=clock)
    calls = 0

    async def fetch() -> SessionRateLimitUsage:
        nonlocal calls
        calls += 1
        return _snapshot()

    await cache.get_or_probe("acct", fetch)
    clock.now = 300.0
    await cache.get_or_probe("acct", fetch)

    assert calls == 2


async def test_coalesces_concurrent_probes() -> None:
    cache = SharedRateLimitProbeCache(ttl_seconds=300.0, clock=_Clock())
    calls = 0
    release = asyncio.Event()

    async def fetch() -> SessionRateLimitUsage:
        nonlocal calls
        calls += 1
        await release.wait()
        return _snapshot()

    waiters = [asyncio.create_task(cache.get_or_probe("acct", fetch)) for _ in range(5)]
    await asyncio.sleep(0)  # let all tasks reach the lock
    release.set()
    results = await asyncio.gather(*waiters)

    assert calls == 1
    assert all(r is results[0] for r in results)


async def test_separate_keys_do_not_share() -> None:
    cache = SharedRateLimitProbeCache(ttl_seconds=300.0, clock=_Clock())
    calls = 0

    async def fetch() -> SessionRateLimitUsage:
        nonlocal calls
        calls += 1
        return _snapshot()

    await cache.get_or_probe("local:~", fetch)
    await cache.get_or_probe("remote:devbox", fetch)

    assert calls == 2


async def test_none_results_are_not_cached() -> None:
    cache = SharedRateLimitProbeCache(ttl_seconds=300.0, clock=_Clock())
    calls = 0

    async def fetch() -> SessionRateLimitUsage | None:
        nonlocal calls
        calls += 1
        return None

    await cache.get_or_probe("acct", fetch)
    await cache.get_or_probe("acct", fetch)

    assert calls == 2


async def test_force_bypasses_cache_hit() -> None:
    clock = _Clock()
    cache = SharedRateLimitProbeCache(ttl_seconds=300.0, clock=clock)
    calls = 0

    async def fetch() -> SessionRateLimitUsage:
        nonlocal calls
        calls += 1
        return _snapshot(used=float(calls))

    await cache.get_or_probe("acct", fetch)
    forced = await cache.get_or_probe("acct", fetch, force=True)

    assert calls == 2
    assert forced is not None
    assert forced.windows[0].used_percent == 2.0


async def test_invalidate_drops_cached_entry() -> None:
    cache = SharedRateLimitProbeCache(ttl_seconds=300.0, clock=_Clock())
    calls = 0

    async def fetch() -> SessionRateLimitUsage:
        nonlocal calls
        calls += 1
        return _snapshot()

    await cache.get_or_probe("acct", fetch)
    cache.invalidate("acct")
    await cache.get_or_probe("acct", fetch)

    assert calls == 2
