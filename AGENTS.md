# Repository Guidelines

## Project Structure & Module Organization
`backend/` contains the FastAPI service. Core code lives in `backend/src/waypoint/`, with API, runtime, auth, storage, and CLI modules split by concern. Backend tests live in `backend/tests/`. `frontend/` contains the Next.js 15 PWA; routes live in `frontend/src/app/`, shared UI in `frontend/src/components/`, and client helpers in `frontend/src/lib/`. `3rdparty/codex/` is a pinned submodule for the local Codex SDK; avoid edits unless you are intentionally updating the dependency.

## Build, Test, and Development Commands
- `git submodule update --init --recursive` initializes the vendored Codex SDK.
- `cd backend && uv sync --group dev` installs backend runtime and test dependencies.
- `cd backend && uv run waypoint serve` starts the API on port `8787` by default.
- `cd backend && uv run pytest` runs the backend test suite.
- `cd frontend && npm install` installs the frontend dependencies.
- `cd frontend && npm run dev` starts the phone-accessible Next.js dev server on `0.0.0.0`.
- `cd frontend && npm run build && npm run start` runs a production-like frontend check.
- `cd frontend && npm run lint` runs the frontend linter.

## Coding Style & Naming Conventions
Follow the existing style in each half of the repo. Python uses 4-space indentation, type hints, top-level imports, and `snake_case` for functions/modules; keep FastAPI handlers and Pydantic models explicit. TypeScript uses 2-space indentation, strict typing, `PascalCase` for components, and `camelCase` for helpers and state. Keep comments sparse and only explain non-obvious reasoning.

## Testing Guidelines
Backend tests use `pytest` and `pytest-asyncio`; place new tests in `backend/tests/` as `test_<feature>.py`. Add focused unit tests for reusable runtime, storage, auth, or API behavior. The frontend currently has no automated test harness in this repo, so at minimum run `npm run lint` and a production build for UI changes.

## Commit & Pull Request Guidelines
Recent commits use short imperative subjects such as `Add terminate and delete endpoints for sessions`. Keep commits narrowly scoped and describe the behavior change, not the implementation mechanics. Agents may create commits automatically when that helps complete a task; split backend, frontend, and docs changes into separate logical commits instead of bundling unrelated work together. PRs should include a concise summary, the commands you ran, linked issues when applicable, and screenshots for frontend changes. Call out any `.env`, Tailscale, or session-runtime setup needed for reviewers.

## Security & Configuration Tips
Start from `backend/.env.example` and `frontend/.env.example`; never commit real secrets. Set a real `WAYPOINT_PASSWORD` locally before exposing the service beyond localhost, and prefer configuration changes through documented env vars like `WAYPOINT_HOST`, `WAYPOINT_PORT`, and `NEXT_ALLOWED_DEV_ORIGINS`.
