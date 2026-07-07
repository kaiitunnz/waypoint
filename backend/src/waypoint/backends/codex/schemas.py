"""Codex-plugin-specific Pydantic models.

The thread summary returned from ``list_threads`` and the import-request
body validated by ``import_request_schema`` live next to the plugin so
``waypoint.schemas`` stays free of per-backend names. The API
dispatcher serialises summaries via ``model_dump`` and validates
import requests via the plugin's ``import_request_schema`` attribute,
so callers don't need to import these types directly.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from waypoint.launch_env import LaunchEnv
from waypoint.schemas import LaunchMode, SessionTransportId


class CodexThreadSummary(BaseModel):
    id: str
    title: str
    cwd: str
    repo_name: str | None = None
    branch: str | None = None
    preview: str | None = None
    created_at: datetime
    updated_at: datetime


class CodexThreadImportRequest(BaseModel):
    thread_id: str
    launch_target_id: str | None = None
    # Mirrors ``SessionCreateRequest.launch_mode``: ``auto`` and
    # ``direct`` resume via the structured Codex app-server protocol,
    # while ``tmux_wrapper`` runs ``codex resume <uuid>`` inside a tmux
    # pane. ``auto`` falls through to tmux when the structured plugin
    # is not available for managed launch, matching create_session.
    launch_mode: LaunchMode = LaunchMode.AUTO
    # Pins the transport the imported thread is driven over, mirroring
    # ``SessionCreateRequest.transport``. ``None`` keeps the
    # ``launch_mode``-derived path; an explicit transport supersedes
    # ``launch_mode`` and must be one the agent declares (the runtime
    # rejects mismatches with 400).
    transport: SessionTransportId | None = None
    launch_env: LaunchEnv = Field(default_factory=dict)
    # Account/config-dir profile used to list/import this thread; persisted so a
    # later resume/delete/history-read uses the same state root.
    account_profile_id: str | None = None
    # When true (default), the prior conversation is replayed into the new
    # session's transcript at import time; when false the transcript starts
    # empty and only the underlying agent resumes its own context.
    import_history: bool = True
