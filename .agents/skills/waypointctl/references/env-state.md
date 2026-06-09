# Environment And State

Common environment variables:

- `WAYPOINT_HOME`: repo root used by `waypointctl --home`.
- `WAYPOINTCTL_DAEMON=1`: route commands through `waypointd`.
- `WAYPOINT_STACK_BACKEND_PORT`: backend port override.
- `WAYPOINT_STACK_FRONTEND_PORT`: frontend port override.
- `WAYPOINT_STACK_CONFIG`: backend config path override.
- `WAYPOINT_STACK_BACKEND_DATA_DIR`: backend data dir override.

State and logs normally live under the waypointctl state dir. Use
`waypointctl doctor` to print resolved paths and daemon socket information.
