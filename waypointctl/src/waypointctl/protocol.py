from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DaemonRequest:
    command: str
    args: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {"command": self.command, "args": list(self.args)}


@dataclass(slots=True)
class DaemonLog:
    stream: str
    line: str

    def to_payload(self) -> dict[str, Any]:
        return {"type": "log", "stream": self.stream, "line": self.line}


@dataclass(slots=True)
class DaemonResult:
    ok: bool
    returncode: int = 0
    error: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DaemonResult":
        return cls(
            ok=bool(payload.get("ok", False)),
            returncode=int(payload.get("returncode", 0)),
            error=payload.get("error"),
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "result",
            "ok": self.ok,
            "returncode": self.returncode,
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload
