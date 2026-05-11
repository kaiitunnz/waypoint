# waypointctl

`waypointctl` is the standalone Waypoint control-plane package.

Install it from this directory with tools such as `uv tool install ./waypointctl` or `pipx install ./waypointctl`.

It anchors the repo checkout from `WAYPOINT_HOME` and exposes:

- a Typer-based CLI
- a local `waypointd` daemon
- control commands for the existing Waypoint stack

