#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV_BIN="${UV_BIN:-$(command -v uv)}"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
LOG_DIR="${HOME}/Library/Logs/Waypoint"
PLIST_TEMPLATE="${ROOT_DIR}/launchd/com.waypoint.agent.plist.template"
PLIST_PATH="${LAUNCH_AGENTS_DIR}/com.waypoint.agent.plist"
ENV_FILE="${ROOT_DIR}/.env"

if [[ -z "${UV_BIN}" ]]; then
  echo "uv is required but was not found on PATH" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Expected backend env file at ${ENV_FILE}" >&2
  exit 1
fi

mkdir -p "${LAUNCH_AGENTS_DIR}" "${LOG_DIR}"

"${UV_BIN}" sync --group dev

PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Expected uv-managed interpreter at ${PYTHON_BIN}" >&2
  exit 1
fi

sed \
  -e "s|{{PYTHON}}|${PYTHON_BIN}|g" \
  -e "s|{{WORKDIR}}|${ROOT_DIR}|g" \
  -e "s|{{LOG_DIR}}|${LOG_DIR}|g" \
  "${PLIST_TEMPLATE}" > "${PLIST_PATH}"

launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl load "${PLIST_PATH}"

echo "Installed LaunchAgent at ${PLIST_PATH}"
