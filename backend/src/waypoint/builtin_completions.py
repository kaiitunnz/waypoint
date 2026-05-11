from waypoint.backends.capabilities import BackendCapabilities
from waypoint.schemas import CommandCompletion, CompletionDispatch

_NEW = CommandCompletion(
    id="waypoint:builtin:new",
    trigger="/",
    replacement="/new ",
    name="new",
    description="Start a new session with the same settings",
    kind="session_control",
    source="waypoint",
    dispatch=CompletionDispatch.FRONTEND_CONTROL,
)
_FORK = CommandCompletion(
    id="waypoint:builtin:fork",
    trigger="/",
    replacement="/fork ",
    name="fork",
    description="Fork this session into a new branch",
    kind="session_control",
    source="waypoint",
    dispatch=CompletionDispatch.FRONTEND_CONTROL,
)


def waypoint_builtin_completions(
    capabilities: BackendCapabilities,
    *,
    trigger: str,
) -> list[CommandCompletion]:
    """Waypoint-level completions surfaced for every plugin.

    Dispatch is ``FRONTEND_CONTROL`` so the frontend handles them locally
    without round-tripping through the backend command pipeline.
    """
    if trigger != "/":
        return []
    completions = [_NEW]
    if capabilities.supports_fork:
        completions.append(_FORK)
    return completions
