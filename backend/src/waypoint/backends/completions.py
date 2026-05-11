from waypoint.backends.capabilities import BackendCapabilities
from waypoint.schemas import CommandCompletion, CompletionDispatch


def static_slash_completions(
    backend_id: str,
    capabilities: BackendCapabilities,
    *,
    prefix: str = "",
) -> list[CommandCompletion]:
    """Map static backend capability slash commands to completion records."""
    if prefix and not prefix.startswith("/"):
        prefix = f"/{prefix}"
    completions: list[CommandCompletion] = []
    for spec in capabilities.slash_commands:
        command = f"/{spec.name}"
        if prefix and not command.startswith(prefix):
            continue
        completions.append(
            CommandCompletion(
                id=f"{backend_id}:builtin:{spec.name}",
                trigger="/",
                replacement=f"{command} ",
                name=spec.name,
                description=spec.description,
                kind="command",
                source="builtin",
                dispatch=CompletionDispatch.PLAIN_TEXT,
                argument_hint=spec.argument_hint,
            )
        )
    return completions
