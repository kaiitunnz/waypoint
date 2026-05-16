"""Claude-Code-plugin-specific Pydantic models.

The thread summary returned from ``list_threads`` and the import-request
body validated by ``import_request_schema`` live next to the plugin so
``waypoint.schemas`` stays free of per-backend names. The API
dispatcher serialises summaries via ``model_dump`` and validates
import requests via the plugin's ``import_request_schema`` attribute,
so callers don't need to import these types directly.
"""

from datetime import datetime

from pydantic import BaseModel

from waypoint.schemas import LaunchMode


class ClaudeThreadSummary(BaseModel):
    id: str
    title: str
    cwd: str
    repo_name: str | None = None
    branch: str | None = None
    preview: str | None = None
    created_at: datetime
    updated_at: datetime


class ClaudeThreadImportRequest(BaseModel):
    thread_id: str
    launch_target_id: str | None = None
    # Mirrors ``SessionCreateRequest.launch_mode``: ``auto`` and
    # ``direct`` resume via the structured Claude SDK protocol, while
    # ``tmux_wrapper`` runs ``claude --resume <uuid>`` inside a tmux
    # pane. ``auto`` falls through to tmux when the structured plugin
    # is not available for managed launch, matching create_session.
    launch_mode: LaunchMode = LaunchMode.AUTO
