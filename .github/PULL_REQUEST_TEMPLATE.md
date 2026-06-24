<!-- markdownlint-disable -->

<!--
PR title format (Conventional Commits — drives release-please):
  type: description
  e.g. feat: add terminate and delete endpoints for sessions
       fix: guard the workspace file fetch race
       ci: add the release workflow
  Breaking changes: use `type!: ...` (e.g. `feat!:`) or add a
  `BREAKING CHANGE:` footer, and describe migration steps below.

Types: feat, fix, docs, refactor, perf, test, build, ci, chore
-->

## Purpose

<!-- What does this PR do? Reference related issues with "Fixes #123" or "Relates to #123". -->

## Changes

<!-- List modified files or groups of files with a brief explanation of each. Split backend / frontend / docs where it helps. -->
<!--
- `backend/src/waypoint/api.py` — add the workspace preview endpoints
- `frontend/src/components/WorkspaceFilesPanel.tsx` — file explorer panel
- `backend/tests/test_workspace_preview.py` — cover the path guard
-->

## Design

<!-- For non-trivial PRs: explain the high-level approach and any alternatives you considered. -->

## Test Plan

<!-- How were these changes validated? Provide commands, sample workflows, or screenshots. -->

## Test Result

<!-- Paste relevant test output, logs, or before/after comparisons. -->

---

<details>
<summary>Pre-submission Checklist</summary>

- [ ] I have read the contribution guidelines in `CONTRIBUTING.md`.
- [ ] My PR title is a Conventional Commit and all my commits are signed off (`git commit -s`).
- [ ] I have run `cd backend && uv run pre-commit run --all-files` and fixed any issues.
- [ ] I have added or updated tests covering my changes (if applicable).
- [ ] I have verified the relevant suites pass locally (`cd backend && uv run pytest`, and `cd waypointctl && uv run pytest` if I touched `waypointctl`).
- [ ] If I touched the coding-agent plugin/transport contract or code that dispatches by plugin id, I checked the affected backends (claude_code, codex, opencode) still work.
- [ ] If I changed the frontend, I ran `cd frontend && npm run lint && npm run build` and kept dark/light theme parity (screenshots optional).
- [ ] If this is a breaking change, I marked the title (`type!:` or a `BREAKING CHANGE:` footer) and described migration steps above.
- [ ] I have updated documentation or config examples (`.env.example`, `backend/waypoint.example.yaml`, `docs/`) if user-facing behavior changed.

</details>
