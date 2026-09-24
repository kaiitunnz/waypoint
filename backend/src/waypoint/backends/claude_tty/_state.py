from dataclasses import dataclass


@dataclass
class PendingTtyApproval:
    approval_id: str
    tool_name: str | None
    target: str | None
    approve_number: int
    decline_number: int | None  # None → send Esc
    signature: str  # debounce key: "tool_name:target:question"
    # An ExitPlanMode "ready to proceed" dialog. Approving it exits plan mode in
    # the TUI, so the transport mirrors ``restore_mode`` into the stored
    # permission mode — the pane already lands there via the pressed option.
    is_plan: bool = False
    restore_mode: str | None = None
