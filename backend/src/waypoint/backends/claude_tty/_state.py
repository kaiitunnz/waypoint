from dataclasses import dataclass

from waypoint.schemas import SessionRecord

# transport_state key: the folder the CLI was launched in, kept once Claude has
# moved the session elsewhere.
LAUNCH_CWD_KEY = "launch_cwd"


def launch_folder(session: SessionRecord) -> str:
    """The folder the session's CLI was launched in."""
    recorded = session.transport_state.get(LAUNCH_CWD_KEY)
    return recorded if isinstance(recorded, str) and recorded else session.cwd


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
