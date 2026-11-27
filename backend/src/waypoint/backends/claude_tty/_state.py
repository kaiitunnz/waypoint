from dataclasses import dataclass

from waypoint.backends.claude_tty.pending_calls import PendingCall


@dataclass
class PendingTtyApproval:
    approval_id: str
    tool_name: str | None
    approve_number: int
    decline_number: int | None  # None → send Esc
    # Which request this is: ``call:<id>``, ``pane:<digest>`` or
    # ``plan:<path>:<digest>``.
    signature: str
    # The digest of the dialog as it was drawn when the ask was raised.
    identity: str = ""
    # The call the ask names; None when it is a reading of the pane.
    call: PendingCall | None = None
    # An ExitPlanMode "ready to proceed" dialog. Approving it exits plan mode in
    # the TUI, so the transport mirrors ``restore_mode`` into the stored
    # permission mode — the pane already lands there via the pressed option.
    is_plan: bool = False
    restore_mode: str | None = None
    # A reply is being delivered, so the pane is about to move.
    replying: bool = False
