from waypoint.backends.opencode import remote


def test_remote_serve_script_is_loaded_from_disk() -> None:
    assert remote._REMOTE_SERVE_SCRIPT_PATH.exists()
    assert remote.REMOTE_SERVE_SCRIPT == remote._REMOTE_SERVE_SCRIPT_PATH.read_text(
        encoding="utf-8"
    )
    assert "__WP_PORT__=" in remote.REMOTE_SERVE_SCRIPT
