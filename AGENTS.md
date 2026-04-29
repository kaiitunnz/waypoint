# Repository Guidelines

## Project Structure & Module Organization
`backend/` contains the FastAPI service. Core code lives in `backend/src/waypoint/`, with API, runtime, auth, storage, and CLI modules split by concern. Backend tests live in `backend/tests/`. `frontend/` contains the Next.js 15 PWA; routes live in `frontend/src/app/`, shared UI in `frontend/src/components/`, and client helpers in `frontend/src/lib/`. `3rdparty/codex/` is a pinned submodule for the local Codex SDK; avoid edits unless you are intentionally updating the dependency.

## Build, Test, and Development Commands
- `git submodule update --init --recursive` initializes the vendored Codex SDK.
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
`./scripts/dev-stack.sh` (run from the repo root) is the canonical way to manage the running stack. Use it whenever the user asks to deploy, bring up, tear down, restart, or check on the app:
- Deploy / bring up: `./scripts/dev-stack.sh start`.
- Teardown: `./scripts/dev-stack.sh stop`.
- Apply code changes: `./scripts/dev-stack.sh restart` — neither half runs with hot reload, so a manual restart is always required after backend or frontend edits, including type-check or lint changes that look invisible.
- Check state during testing: `./scripts/dev-stack.sh status` for health, `./scripts/dev-stack.sh logs [backend|frontend]` for output. Restart before re-testing if you have changed code since the last `start`.
Override ports or paths inline with `WAYPOINT_STACK_BACKEND_PORT`, `WAYPOINT_STACK_FRONTEND_PORT`, `WAYPOINT_STACK_CONFIG`, or `WAYPOINT_STACK_BACKEND_DATA_DIR` rather than editing the script.

## Coding Style & Naming Conventions
Follow the existing style in each half of the repo. Python uses 4-space indentation, type hints, top-level imports, and `snake_case` for functions/modules; keep FastAPI handlers and Pydantic models explicit. TypeScript uses 2-space indentation, strict typing, `PascalCase` for components, and `camelCase` for helpers and state. Keep comments sparse and only explain non-obvious reasoning.

## Testing Guidelines
Backend tests use `pytest` and `pytest-asyncio`; place new tests in `backend/tests/` as `test_<feature>.py`. Add focused unit tests for reusable runtime, storage, auth, or API behavior. Before shipping backend changes, prefer running `uv run pre-commit run --all-files` so formatting, lint, spelling, and mypy stay aligned with the repo hooks. The frontend currently has no automated test harness in this repo, so at minimum run `npm run lint` and a production build for UI changes.

## Commit & Pull Request Guidelines
Recent commits use short imperative subjects such as `Add terminate and delete endpoints for sessions`. Keep commits narrowly scoped and describe the behavior change, not the implementation mechanics. Agents may create commits automatically when that helps complete a task; split backend, frontend, and docs changes into separate logical commits instead of bundling unrelated work together. PRs should include a concise summary, the commands you ran, linked issues when applicable, and screenshots for frontend changes. Call out any `.env`, Tailscale, or session-runtime setup needed for reviewers.

## Issue Tracking
GitHub Issues are the source of truth for active bugs and feature requests. Create one leaf issue per actionable bug or feature request, and group related items with thin tracking issues that link them as checklists. Use lowercase issue-title prefixes: `bug: ...`, `feature request: ...`, and `tracking issue: ...`. Tag bug reports with the `bug` label and feature requests with the `enhancement` label. The pinned tracking issues are `tracking issue: open bugs` (<https://github.com/kaiitunnz/waypoint/issues/4>) and `tracking issue: open feature requests` (<https://github.com/kaiitunnz/waypoint/issues/5>).

## Security & Configuration Tips
Start from `backend/.env.example` and `frontend/.env.example`; never commit real secrets. Set a real `WAYPOINT_PASSWORD` locally before exposing the service beyond localhost, and prefer configuration changes through documented env vars like `WAYPOINT_HOST`, `WAYPOINT_PORT`, and `NEXT_ALLOWED_DEV_ORIGINS`.
