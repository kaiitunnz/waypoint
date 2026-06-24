# Vendored Codex collaboration-mode templates

`plan.md` and `default.md` are copied verbatim from the Codex CLI source tree.
They are the developer-instruction bodies Codex's TUI bundles into its
`builtin_collaboration_mode_presets`; `permission_modes.py` ships the same
bodies so a Waypoint mode switch carries the instructions the TUI would.

They are vendored because the published `openai-codex` PyPI wheel contains only
the Python SDK (`src/openai_codex/`) — the Rust-side templates are not packaged.

## Source

- Upstream: <https://github.com/openai/codex>
- Path: `codex-rs/collaboration-mode-templates/templates/{plan,default}.md`
- Pinned commit when vendored: `8f6a945ec93debbf8e7963b84222b4e007b24a84`
  (`python-v0.1.0b2-51-g8f6a945ec9`)

## Updating

When bumping the Codex CLI binary (`openai-codex-cli-bin`), re-sync these files
from the matching upstream commit so the instruction bodies stay aligned with
the running binary:

```sh
# from a checkout of openai/codex at the target tag/commit
cp codex-rs/collaboration-mode-templates/templates/plan.md    <waypoint>/backend/src/waypoint/backends/codex/collaboration_mode_templates/
cp codex-rs/collaboration-mode-templates/templates/default.md <waypoint>/backend/src/waypoint/backends/codex/collaboration_mode_templates/
```

Then update the pinned commit above and re-run the codex backend tests.
