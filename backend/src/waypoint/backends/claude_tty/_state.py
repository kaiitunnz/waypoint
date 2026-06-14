from dataclasses import dataclass


@dataclass
class PendingTtyApproval:
    approval_id: str
    tool_name: str | None
    target: str | None
    approve_number: int
    decline_number: int | None  # None → send Esc
    signature: str  # debounce key: "tool_name:target:question"
