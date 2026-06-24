# Contributing to Waypoint

Thanks for contributing! This guide covers the conventions our CI enforces.
Project architecture and module conventions live in [`AGENTS.md`](AGENTS.md).

## Development setup

See the [README](README.md) for install options. For a working tree:

- Backend: `cd backend && uv sync --group dev && uv run pre-commit install`
- Frontend: `cd frontend && npm install`
- Control plane: `uv tool install ./waypointctl`

## Pull request titles (Conventional Commits)

We **squash-merge** every PR, so the **PR title becomes the commit on `main`**
and feeds [release-please](docs/RELEASING.md). It must follow Conventional
Commits and is validated by the `Check PR Title` workflow:

```
<type>[(scope)][!]: <description>
```

Allowed types: `feat`, `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`,
`chore`.

- `feat:` bumps the minor and `fix:` bumps the patch (see [docs/RELEASING.md](docs/RELEASING.md)).
- Breaking change: append `!` (e.g. `feat!:`) or add a `BREAKING CHANGE:` footer,
  and describe migration steps in the PR body.

Examples: `feat: add session reaping endpoint`,
`fix: guard the workspace file fetch race`, `ci: add the release workflow`.

## DCO sign-off

All commits must carry a [Developer Certificate of Origin](https://developercertificate.org/)
sign-off, verified by the `Check DCO Sign-off` workflow. Add one with:

```bash
git commit -s    # appends "Signed-off-by: Your Name <you@example.com>"
```

To sign off commits already on your branch:

```bash
git rebase --signoff HEAD~N    # N = number of commits in the PR
```

The sign-off name and email must match the commit author.

## Before opening a PR

- Backend: `cd backend && uv run pre-commit run --all-files` and `uv run pytest`.
- waypointctl: `cd waypointctl && uv run pytest` (and `uv run mypy` if you touched it).
- Frontend: `cd frontend && npm run lint && npm run build`.
- Fill in the PR template and make sure CI (`CI`, `Check PR Title`,
  `Check DCO Sign-off`) is green.

## Releases

Releases are automated by release-please — see [docs/RELEASING.md](docs/RELEASING.md).
