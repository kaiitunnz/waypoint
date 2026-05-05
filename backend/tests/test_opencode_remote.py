import asyncio
from pathlib import Path

import pytest

from waypoint.backends.opencode import remote


def test_remote_serve_script_is_loaded_from_disk() -> None:
    assert remote._REMOTE_SERVE_SCRIPT_PATH.exists()
    assert remote.REMOTE_SERVE_SCRIPT == remote._REMOTE_SERVE_SCRIPT_PATH.read_text(
        encoding="utf-8"
    )
    assert "__WP_PORT__=" in remote.REMOTE_SERVE_SCRIPT


@pytest.mark.asyncio
async def test_remote_serve_script_honors_configured_binary(tmp_path: Path) -> None:
    # Regression for the `$0` vs `$1` argv-binding bug: `bash -c SCRIPT BIN`
    # binds BIN to $0, so the script must read ${0:-opencode}, not ${1:-...}.
    argv_log = tmp_path / "argv.log"
    stub = tmp_path / "opencode_stub"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$0 $*\" > '{argv_log}'\n"
        'echo "listening on http://127.0.0.1:9999"\n'
        "sleep 5\n"
    )
    stub.chmod(0o755)

    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        remote.REMOTE_SERVE_SCRIPT,
        str(stub),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None

    try:
        line_bytes = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        assert line_bytes.decode().strip() == "__WP_PORT__=9999"
        assert argv_log.exists()
        recorded = argv_log.read_text().strip()
        assert recorded.startswith(str(stub))
        assert "serve --hostname=127.0.0.1 --port=0" in recorded
    finally:
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except TimeoutError:
            proc.kill()
            await proc.wait()
