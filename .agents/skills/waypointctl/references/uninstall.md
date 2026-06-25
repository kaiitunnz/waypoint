# Uninstall

```bash
waypointctl uninstall          # stop, remove checkout + tool, keep data
waypointctl uninstall --purge  # also wipe the state and data directories
waypointctl uninstall --yes    # skip the confirmation prompt
```

`uninstall` stops the stack and daemon, strips the `WAYPOINT_HOME` block from the
shell profiles, removes the installer-managed checkout, and uninstalls the
`waypointctl` tool. It is **destructive** — confirm with the user first, and
treat it like a full-stack stop (it interrupts every running session, including
this assistant).

Safety rails baked into the command:

- The checkout is removed only when it is installer-managed (`git config
  waypoint.managed == true`); a development clone is left in place unless
  `--force` is passed.
- If the backend data dir lives **inside** the checkout, the checkout is kept
  (with a warning) so the data survives — `--purge` overrides this and removes
  both.
- Without `--purge`, the state dir and backend data dir are preserved.

The command runs `scripts/uninstall.sh` from a temporary copy so deleting the
checkout cannot interrupt it. A host installed via `curl | bash` can run that
script directly: `bash scripts/uninstall.sh --home "$WAYPOINT_HOME" [--purge]`.
