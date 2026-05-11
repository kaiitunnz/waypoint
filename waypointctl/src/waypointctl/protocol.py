from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DaemonRequest:
    command: str
    args: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {"command": self.command, "args": list(self.args)}


@dataclass(slots=True)
class DaemonResponse:
    ok: bool
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "DaemonResponse":
        return cls(
            ok=bool(payload.get("ok", False)),
            returncode=int(payload.get("returncode", 0)),
            stdout=str(payload.get("stdout", "")),
            stderr=str(payload.get("stderr", "")),
            error=payload.get("error"),
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload
