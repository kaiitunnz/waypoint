from waypoint.session_presence import SessionPresenceRegistry


def test_touch_makes_session_active() -> None:
    reg = SessionPresenceRegistry(lease_seconds=45)
    assert reg.is_active("s1", now=0.0) is False
    reg.touch("s1", "v1", now=0.0)
    assert reg.is_active("s1", now=0.0) is True


def test_lease_expires_after_ttl() -> None:
    reg = SessionPresenceRegistry(lease_seconds=45)
    reg.touch("s1", "v1", now=0.0)
    assert reg.is_active("s1", now=44.0) is True
    # Boundary is inclusive: a lease at exactly its expiry is gone.
    assert reg.is_active("s1", now=45.0) is False


def test_renew_extends_lease() -> None:
    reg = SessionPresenceRegistry(lease_seconds=45)
    reg.touch("s1", "v1", now=0.0)
    reg.touch("s1", "v1", now=30.0)
    assert reg.is_active("s1", now=70.0) is True


def test_release_drops_only_its_viewer() -> None:
    reg = SessionPresenceRegistry(lease_seconds=45)
    reg.touch("s1", "v1", now=0.0)
    reg.touch("s1", "v2", now=0.0)
    reg.release("s1", "v1")
    assert reg.is_active("s1", now=0.0) is True
    reg.release("s1", "v2")
    assert reg.is_active("s1", now=0.0) is False


def test_release_is_idempotent_for_unknown() -> None:
    reg = SessionPresenceRegistry()
    reg.release("nope", "nobody")  # no raise
    assert reg.is_active("nope") is False


def test_two_viewers_keep_session_active_until_last_expires() -> None:
    reg = SessionPresenceRegistry(lease_seconds=45)
    reg.touch("s1", "v1", now=0.0)
    reg.touch("s1", "v2", now=20.0)
    # v1 expired at 45, v2 still valid until 65.
    assert reg.is_active("s1", now=50.0) is True
    assert reg.is_active("s1", now=65.0) is False


def test_sessions_are_isolated() -> None:
    reg = SessionPresenceRegistry(lease_seconds=45)
    reg.touch("s1", "v1", now=0.0)
    assert reg.is_active("s2", now=0.0) is False


def test_drop_session_forgets_leases() -> None:
    reg = SessionPresenceRegistry(lease_seconds=45)
    reg.touch("s1", "v1", now=0.0)
    reg.drop_session("s1")
    assert reg.is_active("s1", now=0.0) is False


def test_clear_forgets_everything() -> None:
    reg = SessionPresenceRegistry(lease_seconds=45)
    reg.touch("s1", "v1", now=0.0)
    reg.touch("s2", "v2", now=0.0)
    reg.clear()
    assert reg.is_active("s1", now=0.0) is False
    assert reg.is_active("s2", now=0.0) is False
