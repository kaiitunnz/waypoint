import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Header, HTTPException, status

from waypoint.config import Settings
from waypoint.schemas import LoginResponse
from waypoint.storage import Storage


class TokenStore:
    def __init__(self, settings: Settings, storage: Storage) -> None:
        self.settings = settings
        self.storage = storage
        self.storage.purge_expired_tokens(datetime.now(UTC))

    def issue(self) -> LoginResponse:
        expires_at = datetime.now(UTC) + timedelta(
            seconds=self.settings.token_ttl_seconds
        )
        token = secrets.token_urlsafe(32)
        self.storage.insert_token(token, expires_at)
        return LoginResponse(token=token, expires_at=expires_at)

    def validate(self, token: str) -> bool:
        expires_at = self.storage.get_token_expiry(token)
        if expires_at is None:
            return False
        now = datetime.now(UTC)
        if expires_at < now:
            self.storage.delete_token(token)
            return False
        # Sliding refresh: extend the expiry once more than half the TTL has
        # been consumed so an actively-used token never silently lapses while
        # the user is away long enough for iOS to evict localStorage.
        ttl = timedelta(seconds=self.settings.token_ttl_seconds)
        if expires_at - now < ttl / 2:
            self.storage.refresh_token_expiry(token, now + ttl)
        return True


def parse_bearer_token(value: str | None) -> str | None:
    if not value:
        return None
    prefix = "Bearer "
    if not value.startswith(prefix):
        return None
    return value[len(prefix) :]


def require_token(
    authorization: Annotated[str | None, Header()] = None,
    token_store: TokenStore | None = None,
) -> str:
    if token_store is None:
        raise RuntimeError("token store dependency not bound")
    token = parse_bearer_token(authorization)
    if token is None or not token_store.validate(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
        )
    return token
