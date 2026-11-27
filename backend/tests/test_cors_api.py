"""CORS headers the web apps rely on."""

from pathlib import Path

import httpx

from waypoint.api import create_app
from waypoint.settings import Settings


async def test_cross_origin_clients_can_read_the_date_header(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/health", headers={"Origin": "http://localhost:3000"}
        )
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "Date" in response.headers["access-control-expose-headers"]
