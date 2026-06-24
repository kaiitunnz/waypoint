# Repository Guidelines

## Project Structure & Module Organization
`backend/` contains the FastAPI service. Core code lives in `backend/src/waypoint/`, with API, runtime, auth, storage, and CLI modules split by concern. Backend tests live in `backend/tests/`. `frontend/` contains the Next.js 15 PWA; routes live in `frontend/src/app/`, shared UI in `frontend/src/components/`, and client helpers in `frontend/src/lib/`. The Codex backend uses the `openai-codex` Python SDK from PyPI (pinned in `backend/pyproject.toml`); the Codex collaboration-mode templates it needs are vendored under `backend/src/waypoint/backends/codex/collaboration_mode_templates/` since the wheel does not ship them.

Coding-agent backends live in `backend/src/waypoint/backends/<id>/` (currently `claude_code/`, `codex/`, `opencode/`). A session is an (agent, transport) pair: the AgentPlugin (claude_code, codex, opencode) owns the protocol, discovery, normalizer, and the AgentLaunchContract, while Transports (like the native structured adapter per agent, generic tmux, or tty-tail aka claude_tty) own send, interrupt, approval, lifecycle, and flags like is_structured and supports_resume. Each package owns its plugin descriptor, transport adapter, protocol driver, event normalizer, and backend-specific helpers — there are **no** claude/codex/opencode/tmux modules at the top level of `waypoint/`. The runtime, API, scheduler, and frontend dispatch by plugin id; never add a per-backend `if` branch in those files or the transports. The contract, capability descriptor, and a full extension recipe live in [`docs/coding_agent_plugins.md`](docs/coding_agent_plugins.md). Read it before touching anything that mentions Claude, Codex, or OpenCode by name, and before adding a new backend.

## Build, Test, and Development Commands
- `cd backend && uv sync --group dev` installs backend runtime and test dependencies.
- `cd backend && uv run pre-commit install` installs the backend git hooks for `black`, `isort`, `ruff`, `codespell`, and `mypy`.
- `cd backend && uv run waypoint serve` starts the API on port `8787` by default.
- `cd backend && uv run pytest` runs the backend test suite.
- `cd backend && uv run pre-commit run --all-files` runs the backend formatting and type/lint checks on demand.
- `cd frontend && npm install` installs the frontend dependencies.
- `cd frontend && npm run dev` starts the phone-accessible Next.js dev server on `0.0.0.0`.
- `cd frontend && npm run build && npm run start` runs a production-like frontend check.
- `cd frontend && npm run lint` runs the frontend linter.

## Local Stack Supervisor
`waypointctl` (installed via `uv tool install ./waypointctl` or `pipx install ./waypointctl`) is the canonical way to manage the running stack. Use it whenever the user asks to deploy, bring up, tear down, restart, or check on the app:
- Deploy / bring up: `waypointctl start`.
- Teardown: `waypointctl stop`.
- Apply code changes: `waypointctl restart [backend|frontend]` — `waypointctl restart` and `waypointctl restart backend` are allowed, but you must have explicit permission from the user for this session before running them. If the user hasn't given that permission yet, pause and ask. `waypointctl restart frontend` can be run safely when the frontend code is updated.
- Check state during testing: `waypointctl status` for health, `waypointctl logs [backend|frontend]` for output. Restart before re-testing if you have changed code since the last `start`.
Override ports or paths inline with `WAYPOINT_STACK_BACKEND_PORT`, `WAYPOINT_STACK_FRONTEND_PORT`, `WAYPOINT_STACK_CONFIG`, or `WAYPOINT_STACK_BACKEND_DATA_DIR` rather than editing config files; values may also live in `.env` at the repo root.

`scripts/waypoint.sh` is the legacy supervisor and is slated for deprecation; prefer `waypointctl` for new work and only fall back to the script if the user explicitly asks for it.

## Distributing Coding-Agent Skills
The repo's `.agents/skills/` are auto-installed only into the personal assistant's workspace. To let *any* coding session spawn child Waypoint sessions (`waypoint-subagents`), message other live sessions (`waypoint-comms`), orchestrate large parallel batch jobs over the blackboard (`waypoint-workqueue`), and commit safely in the shared multi-session working tree (`waypoint-worktree`), install those skills into the cross-agent global skills directory (`~/.agents/skills` by default) with `scripts/install_skills.sh install` (or `waypointctl skills install`); all four ship in the default selection. The installer symlinks by default so the skills track the repo; pass `--skill-dir` / `WAYPOINT_SKILLS_DIR` to target an agent-specific directory (e.g. `~/.claude/skills`, `~/.codex/skills`), `--skill`/`--all` to widen the selection, and `--copy` for a detached snapshot. `status` and `uninstall` round-trip it; `uninstall` only removes symlinks the installer created, never a real or copied directory.

## Coding Style & Naming Conventions
Follow the existing style in each half of the repo. Python uses 4-space indentation, type hints, top-level imports, and `snake_case` for functions/modules; keep FastAPI handlers and Pydantic models explicit. TypeScript uses 2-space indentation, strict typing, `PascalCase` for components, and `camelCase` for helpers and state. Keep comments sparse and only explain non-obvious reasoning.

## Frontend Theming
The app supports dark (default) and light themes via the `html[data-theme="light"]` attribute set by `ThemeToggle`. All CSS lives in `frontend/src/app/globals.css`, which is organized into named sections with `/* ─── Section ─── */` dividers; light-mode overrides are co-located immediately after the base rule they override.

**When adding or editing styles**, you must maintain parity between themes:
- `:root` defines CSS variables for dark mode. Variables like `--bg-card`, `--line`, `--text`, `--success`, etc. resolve automatically in both themes — use them for anything that should adapt without extra work.
- Components that use **hardcoded dark RGBA values** (e.g. `background: rgba(8,11,16,0.35)`) will not adapt automatically. Every such rule needs a matching `html[data-theme="light"] .selector { ... }` override placed directly below it.
- After adding any new component styles, scan for hardcoded dark RGBA values and add the corresponding light override before committing.

## Testing Guidelines
Backend tests use `pytest` and `pytest-asyncio`; place new tests in `backend/tests/` as `test_<feature>.py`. Add focused unit tests for reusable runtime, storage, auth, or API behavior. Before shipping backend changes, prefer running `uv run pre-commit run --all-files` so formatting, lint, spelling, and mypy stay aligned with the repo hooks. The frontend currently has no automated test harness in this repo, so at minimum run `npm run lint` and a production build for UI changes.

## Commit & Pull Request Guidelines
Recent commits use short imperative subjects such as `Add terminate and delete endpoints for sessions`. Keep commits narrowly scoped and describe the behavior change, not the implementation mechanics. Agents may create commits automatically when that helps complete a task; split backend, frontend, and docs changes into separate logical commits instead of bundling unrelated work together. PRs should include a concise summary, the commands you ran, linked issues when applicable, and screenshots for frontend changes when helpful. Call out any `.env`, Tailscale, or session-runtime setup needed for reviewers. PRs are squash-merged, so the PR title must be a Conventional Commit (`feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`) — it becomes the commit on `main` and drives release-please — and every commit must carry a DCO sign-off (`git commit -s`). See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Issue Tracking
GitHub Issues are the source of truth for active bugs and feature requests. Create one leaf issue per actionable bug or feature request, and group related items with thin tracking issues that link them as checklists. Use lowercase issue-title prefixes: `bug: ...`, `feature request: ...`, and `tracking issue: ...`. Tag bug reports with the `bug` label and feature requests with the `enhancement` label. The pinned tracking issues are `tracking issue: open bugs` (<https://github.com/kaiitunnz/waypoint/issues/4>) and `tracking issue: open feature requests` (<https://github.com/kaiitunnz/waypoint/issues/5>).

## Security & Configuration Tips
Start from `backend/.env.example` and `frontend/.env.example`; never commit real secrets. Set a real `WAYPOINT_PASSWORD` locally before exposing the service beyond localhost, and prefer configuration changes through documented env vars like `WAYPOINT_HOST`, `WAYPOINT_PORT`, and `NEXT_ALLOWED_DEV_ORIGINS`.
