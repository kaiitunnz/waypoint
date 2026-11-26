"""The password Waypoint gives each OpenCode server it starts."""

import base64
import secrets
from dataclasses import dataclass, field
from typing import Self

USERNAME = "opencode"


@dataclass(frozen=True)
class ServerAuth:
    password: str = field(repr=False)

    @classmethod
    def generate(cls) -> Self:
        return cls(secrets.token_urlsafe(32))

    def server_env(self) -> dict[str, str]:
        # OpenCode 1.x reads OPENCODE_SERVER_PASSWORD and lets the user name be
        # changed; 2.x reads either password variable and fixes the name.
        return {
            "OPENCODE_SERVER_USERNAME": USERNAME,
            "OPENCODE_SERVER_PASSWORD": self.password,
            "OPENCODE_PASSWORD": self.password,
        }

    @property
    def basic_token(self) -> str:
        return base64.b64encode(f"{USERNAME}:{self.password}".encode()).decode()
