# Releasing

## Overview

Releases are managed by [release-please](https://github.com/googleapis/release-please). It reads conventional-commit subjects on `main`, maintains a "Release PR" that accumulates pending changes, and cuts a GitHub Release when that PR is merged.

## Conventional commits

Commit subjects drive version bumps and `CHANGELOG.md` entries:

| Subject prefix | Effect |
| --- | --- |
| `fix:` | patch bump |
| `feat:` | minor bump |
| `feat!:` or any type with `BREAKING CHANGE:` in the footer | major bump |
| `chore:`, `docs:`, `refactor:`, `test:`, `ci:`, etc. | no version bump |

While the project is pre-1.0, `bump-minor-pre-major: true` and `bump-patch-for-minor-pre-major: true` are set in `release-please-config.json` — `feat:` bumps the minor and `fix:` bumps the patch on 0.x, which matches normal semver expectations.

## Release flow

1. Merge commits to `main` with conventional-commit subjects.
2. release-please opens (or updates) a Release PR targeting `main`. The PR bumps the version in `CHANGELOG.md` and `frontend/package.json`.
3. Merge the Release PR. release-please creates a `vX.Y.Z` git tag and publishes a GitHub Release with the corresponding `CHANGELOG.md` section.

No manual tagging or version-file editing is required.

## Versioning

All three packages share a single version number derived from the root git tag:

- **Backend** (`backend/`) and **waypointctl** (`waypointctl/`) both declare `dynamic = ["version"]` with `[tool.setuptools_scm] root = ".."`. At build time, `setuptools-scm` reads the nearest `vX.Y.Z` tag from the repo root.
- **Frontend** (`frontend/`) has its `package.json` version updated directly by release-please via the `extra-files` entry in `release-please-config.json`.

The tag format is `vX.Y.Z` with no component prefix (`include-component-in-tag: false` in `release-please-config.json`).

## Upgrading existing installations

```bash
waypointctl self-update                   # upgrade to the latest tag
waypointctl self-update --ref vX.Y.Z      # pin to a specific tag
```

`self-update` fetches new tags, checks out the target ref, reinstalls `waypointctl` via `uv tool install --force`, sets `WAYPOINT_STACK_FORCE_FRONTEND_BUILD=1`, and restarts the stack.
