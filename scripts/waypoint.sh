#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
STATE_DIR="${ROOT_DIR}/tmp/waypoint"
RUN_DIR="${STATE_DIR}/run"
LOG_DIR="${STATE_DIR}/logs"
ENV_FILE="${ROOT_DIR}/.env"

BACKEND_HOST=""
BACKEND_PORT=""
BACKEND_CONFIG=""
BACKEND_DATA_DIR=""
FRONTEND_PORT=""
FRONTEND_DEV=""
START_TIMEOUT=""
UV_CACHE_DIR=""

BACKEND_LOG="${LOG_DIR}/backend.log"
FRONTEND_LOG="${LOG_DIR}/frontend.log"

usage() {
  cat <<'EOF'
Usage: scripts/waypoint.sh <command> [service]

Commands:
  pwd             Print the repository root
  start           Start backend and frontend in the background
  stop            Stop backend and frontend
  restart [service] Restart backend, frontend, or both
  status          Show PID, port, and health for both services
  logs [service]  Tail logs for backend, frontend, or both

Environment overrides:
  WAYPOINT_STACK_BACKEND_HOST      Default: 0.0.0.0
  WAYPOINT_STACK_BACKEND_PORT      Default: 8787
  WAYPOINT_STACK_CONFIG            Default: backend/waypoint.yaml
  WAYPOINT_STACK_BACKEND_DATA_DIR  Default: tmp/waypoint/backend-data
  WAYPOINT_STACK_FRONTEND_PORT     Default: 3000
  WAYPOINT_STACK_START_TIMEOUT     Default: 30
  WAYPOINT_STACK_UV_CACHE_DIR      Default: tmp/waypoint/uv-cache
  WAYPOINT_STACK_FORCE_FRONTEND_BUILD=1  Always rebuild the frontend (default: skip when up to date)
  WAYPOINT_STACK_CAFFEINATE=0      Disable the macOS sleep inhibitor (default: engaged on Darwin)

The script loads ${ENV_FILE} if it exists before applying defaults.
EOF
}

load_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
  fi
}

print_root_dir() {
  printf '%s\n' "${ROOT_DIR}"
}

init_config() {
  BACKEND_HOST="${WAYPOINT_STACK_BACKEND_HOST:-0.0.0.0}"
  BACKEND_PORT="${WAYPOINT_STACK_BACKEND_PORT:-8787}"
  BACKEND_CONFIG="${WAYPOINT_STACK_CONFIG:-${BACKEND_DIR}/waypoint.yaml}"
  BACKEND_DATA_DIR="${WAYPOINT_STACK_BACKEND_DATA_DIR:-${STATE_DIR}/backend-data}"
  FRONTEND_PORT="${WAYPOINT_STACK_FRONTEND_PORT:-3000}"
  FRONTEND_DEV="${WAYPOINT_STACK_FRONTEND_DEV:-0}"
  START_TIMEOUT="${WAYPOINT_STACK_START_TIMEOUT:-30}"
  UV_CACHE_DIR="${WAYPOINT_STACK_UV_CACHE_DIR:-${STATE_DIR}/uv-cache}"

  BACKEND_CONFIG="$(resolve_path "${BACKEND_CONFIG}")"
  BACKEND_DATA_DIR="$(resolve_path "${BACKEND_DATA_DIR}")"
  UV_CACHE_DIR="$(resolve_path "${UV_CACHE_DIR}")"
}

resolve_path() {
  local raw="$1"
  if [[ "${raw}" = /* ]]; then
    echo "${raw}"
  else
    echo "${ROOT_DIR}/${raw}"
  fi
}

ensure_state_dirs() {
  mkdir -p "${RUN_DIR}" "${LOG_DIR}" "${BACKEND_DATA_DIR}" "${UV_CACHE_DIR}"
}

require_command() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "Missing required command: ${cmd}" >&2
    exit 1
  fi
}

pid_file_for() {
  local service="$1"
  echo "${RUN_DIR}/${service}.pid"
}

log_file_for() {
  local service="$1"
  echo "${LOG_DIR}/${service}.log"
}

started_marker_for() {
  local service="$1"
  echo "${RUN_DIR}/${service}.started-this-run"
}

# Repo-relative paths that drive the Next build. Listed once and shared
# by both the mtime check (resolved to absolute paths) and the git diff
# check (kept relative for git pathspec).
FRONTEND_BUILD_INPUTS_RELATIVE=(
  "frontend/src"
  "frontend/public"
  "frontend/package.json"
  "frontend/package-lock.json"
  "frontend/next.config.ts"
  "frontend/tsconfig.json"
)

# Skip `next build` when nothing the build cares about has changed.
# .next/ stays on disk between runs, so a hit is "do nothing" — no
# cache to restore — which beats general-purpose build runners on this
# single-package workflow. Three layers:
#   1. If recorded HEAD (.next/BUILD_REF) matches current HEAD, the
#      tree's committed state is identical to last build — skip
#      straight to the mtime check for uncommitted edits.
#   2. If HEAD has moved, ask git which paths changed between the two
#      refs. If none of them are build inputs (docs / backend / CI),
#      the build output is still valid; bump BUILD_REF forward and
#      proceed to the mtime check.
#   3. mtime fallback: compares input file mtimes against BUILD_ID for
#      uncommitted changes that don't move HEAD.
# Force a full rebuild with WAYPOINT_STACK_FORCE_FRONTEND_BUILD=1 or
# `rm -rf frontend/.next`.
frontend_build_ref_file() {
  echo "${FRONTEND_DIR}/.next/BUILD_REF"
}

current_git_head() {
  git -C "${ROOT_DIR}" rev-parse HEAD 2>/dev/null || true
}

frontend_build_is_fresh() {
  if [[ "${WAYPOINT_STACK_FORCE_FRONTEND_BUILD:-0}" == "1" ]]; then
    return 1
  fi
  local marker="${FRONTEND_DIR}/.next/BUILD_ID"
  [[ -f "${marker}" ]] || return 1

  # Ref check, gated on both sides existing — manual `npm run build`
  # outside this script never wrote BUILD_REF, so we fall back to the
  # mtime check for that case.
  local ref_file current_ref last_ref
  ref_file="$(frontend_build_ref_file)"
  if [[ -f "${ref_file}" ]]; then
    current_ref="$(current_git_head)"
    last_ref="$(cat "${ref_file}" 2>/dev/null || true)"
    if [[ -n "${current_ref}" && -n "${last_ref}" && "${current_ref}" != "${last_ref}" ]]; then
      local changed
      if changed=$(git -C "${ROOT_DIR}" diff --name-only \
            "${last_ref}" "${current_ref}" -- \
            "${FRONTEND_BUILD_INPUTS_RELATIVE[@]}" 2>/dev/null); then
        if [[ -n "${changed}" ]]; then
          return 1
        fi
        # Build inputs untouched between the two refs. The output is
        # still valid for current HEAD — record that so the next run
        # doesn't redo this diff.
        record_frontend_build_ref
      else
        # Diff failed (e.g. last_ref orphaned by a rebase). Play safe.
        return 1
      fi
    fi
  fi

  local inputs=()
  local rel
  for rel in "${FRONTEND_BUILD_INPUTS_RELATIVE[@]}"; do
    [[ -e "${ROOT_DIR}/${rel}" ]] && inputs+=("${ROOT_DIR}/${rel}")
  done
  (( ${#inputs[@]} > 0 )) || return 1

  # `-print -quit` bails at the first newer file so we don't walk the
  # whole src tree on a hit.
  local newer
  newer=$(find "${inputs[@]}" -newer "${marker}" -print -quit 2>/dev/null) || true
  [[ -z "${newer}" ]]
}

# Stamp the ref of the tree the current .next/ output reflects.
# Best-effort: silently does nothing outside a git repo.
record_frontend_build_ref() {
  local current_ref
  current_ref="$(current_git_head)"
  [[ -n "${current_ref}" ]] || return 0
  printf '%s\n' "${current_ref}" >"$(frontend_build_ref_file)"
}

read_pid() {
  local service="$1"
  local pid_file
  pid_file="$(pid_file_for "${service}")"
  if [[ -f "${pid_file}" ]]; then
    cat "${pid_file}"
  fi
}

is_pid_running() {
  local pid="$1"
  kill -0 "${pid}" >/dev/null 2>&1
}

clear_stale_pid() {
  local service="$1"
  local pid
  pid="$(read_pid "${service}")"
  if [[ -n "${pid}" ]] && ! is_pid_running "${pid}"; then
    rm -f "$(pid_file_for "${service}")"
  fi
}

is_service_running() {
  local service="$1"
  local pid
  clear_stale_pid "${service}"
  pid="$(read_pid "${service}")"
  [[ -n "${pid}" ]] && is_pid_running "${pid}"
}

port_in_use() {
  local port="$1"
  lsof -nP -iTCP:"${port}" -sTCP:LISTEN -t >/dev/null 2>&1
}

wait_for_http() {
  local service="$1"
  local url="$2"
  local pid="$3"
  local deadline=$((SECONDS + START_TIMEOUT))

  while (( SECONDS < deadline )); do
    if curl --silent --fail --max-time 2 "${url}" >/dev/null 2>&1; then
      return 0
    fi
    if ! is_pid_running "${pid}"; then
      return 1
    fi
    sleep 1
  done

  return 1
}

tail_recent_log() {
  local service="$1"
  local log_file
  log_file="$(log_file_for "${service}")"

  if [[ -f "${log_file}" ]]; then
    echo "--- ${service} log ---" >&2
    tail -n 40 "${log_file}" >&2
  fi
}

start_backend() {
  ensure_state_dirs
  clear_stale_pid "backend"

  if is_service_running "backend"; then
    echo "backend already running (pid $(read_pid backend))"
    return 0
  fi

  if port_in_use "${BACKEND_PORT}"; then
    echo "backend port ${BACKEND_PORT} is already in use" >&2
    return 1
  fi

  : >"$(started_marker_for backend)"
  : >"${BACKEND_LOG}"
  echo "starting backend on ${BACKEND_HOST}:${BACKEND_PORT}"
  (
    cd "${BACKEND_DIR}"
    nohup env \
      WAYPOINT_CONFIG_PATH="${BACKEND_CONFIG}" \
      WAYPOINT_HOST="${BACKEND_HOST}" \
      WAYPOINT_PORT="${BACKEND_PORT}" \
      WAYPOINT_DATA_DIR="${BACKEND_DATA_DIR}" \
      UV_CACHE_DIR="${UV_CACHE_DIR}" \
      uv run waypoint serve >>"${BACKEND_LOG}" 2>&1 &
    echo $! >"$(pid_file_for backend)"
  )

  if ! wait_for_http "backend" "http://127.0.0.1:${BACKEND_PORT}/health" "$(read_pid backend)"; then
    echo "backend failed to become healthy" >&2
    tail_recent_log "backend"
    return 1
  fi
}

start_frontend() {
  ensure_state_dirs
  clear_stale_pid "frontend"

  if is_service_running "frontend"; then
    echo "frontend already running (pid $(read_pid frontend))"
    return 0
  fi

  if port_in_use "${FRONTEND_PORT}"; then
    echo "frontend port ${FRONTEND_PORT} is already in use" >&2
    return 1
  fi

  : >"$(started_marker_for frontend)"
  : >"${FRONTEND_LOG}"

  if [[ "${FRONTEND_DEV}" == "1" ]]; then
    echo "starting frontend in development mode on 0.0.0.0:${FRONTEND_PORT}"
    (
      cd "${FRONTEND_DIR}"
      env PORT="${FRONTEND_PORT}" npm run dev >>"${FRONTEND_LOG}" 2>&1 &
      echo $! >"$(pid_file_for frontend)"
    )
  else
    if frontend_build_is_fresh; then
      echo "frontend build up to date, skipping rebuild"
    else
      echo "building frontend"
      (
        cd "${FRONTEND_DIR}"
        env PORT="${FRONTEND_PORT}" npm run build >>"${FRONTEND_LOG}" 2>&1
      )
      record_frontend_build_ref
    fi

    echo "starting frontend on 0.0.0.0:${FRONTEND_PORT}"
    (
      cd "${FRONTEND_DIR}"
      nohup env PORT="${FRONTEND_PORT}" npm run start >>"${FRONTEND_LOG}" 2>&1 &
      echo $! >"$(pid_file_for frontend)"
    )
  fi

  if ! wait_for_http "frontend" "http://127.0.0.1:${FRONTEND_PORT}" "$(read_pid frontend)"; then
    echo "frontend failed to become healthy" >&2
    tail_recent_log "frontend"
    return 1
  fi
}

wait_for_exit() {
  local pid="$1"
  local deadline=$((SECONDS + 10))

  while is_pid_running "${pid}" && (( SECONDS < deadline )); do
    sleep 1
  done

  ! is_pid_running "${pid}"
}

# Recursively print descendant PIDs of ${1}, deepest first.
collect_descendants() {
  local parent="$1"
  local children child
  children=$(pgrep -P "${parent}" 2>/dev/null) || true
  for child in ${children}; do
    collect_descendants "${child}"
    printf '%s\n' "${child}"
  done
}

# Stop a process and every descendant. We track only the npm/uv leader
# in pid files, but `npm run start` forks `sh -c 'next start'` which
# forks `node next-server`; on Linux the leader's signal forwarder
# doesn't always propagate before its own death, so the next-server
# grandchild gets reparented to PID 1 and keeps the port. Walking the
# tree avoids relying on npm's forwarding behavior.
kill_process_tree() {
  local pid="$1"
  local descendants
  descendants=$(collect_descendants "${pid}")

  # SIGTERM children first so the leader sees them gone via SIGCHLD
  # and shuts down cleanly, then signal the leader itself.
  if [[ -n "${descendants}" ]]; then
    while IFS= read -r d; do
      [[ -n "${d}" ]] && kill "${d}" >/dev/null 2>&1 || true
    done <<<"${descendants}"
  fi
  kill "${pid}" >/dev/null 2>&1 || true

  if wait_for_exit "${pid}"; then
    if [[ -n "${descendants}" ]]; then
      while IFS= read -r d; do
        if [[ -n "${d}" ]] && is_pid_running "${d}"; then
          kill -9 "${d}" >/dev/null 2>&1 || true
        fi
      done <<<"${descendants}"
    fi
    return 0
  fi

  if [[ -n "${descendants}" ]]; then
    while IFS= read -r d; do
      [[ -n "${d}" ]] && kill -9 "${d}" >/dev/null 2>&1 || true
    done <<<"${descendants}"
  fi
  kill -9 "${pid}" >/dev/null 2>&1 || true
}

# macOS-only sleep inhibitor. We hold a `caffeinate -i -s` process for
# the lifetime of the stack so scheduled sessions and phone clients
# don't lose the host on idle/system sleep. `-d`/`-m` are intentionally
# omitted: display sleep and disk idle sleep are fine. Disable via
# WAYPOINT_STACK_CAFFEINATE=0; no-op on non-Darwin platforms.
caffeinate_enabled() {
  [[ "$(uname)" == "Darwin" ]] \
    && [[ "${WAYPOINT_STACK_CAFFEINATE:-1}" == "1" ]] \
    && command -v caffeinate >/dev/null 2>&1
}

start_caffeinate() {
  caffeinate_enabled || return 0
  clear_stale_pid "caffeinate"
  if is_service_running "caffeinate"; then
    return 0
  fi
  echo "engaging caffeinate to keep the system awake while the stack runs"
  nohup caffeinate -i -s >/dev/null 2>&1 &
  echo $! >"$(pid_file_for caffeinate)"
}

stop_caffeinate() {
  clear_stale_pid "caffeinate"
  local pid
  pid="$(read_pid caffeinate)"
  if [[ -z "${pid}" ]]; then
    return 0
  fi
  echo "stopping caffeinate (pid ${pid})"
  kill "${pid}" >/dev/null 2>&1 || true
  if ! wait_for_exit "${pid}"; then
    kill -9 "${pid}" >/dev/null 2>&1 || true
  fi
  rm -f "$(pid_file_for caffeinate)"
}

status_caffeinate() {
  [[ "$(uname)" == "Darwin" ]] || return 0
  clear_stale_pid "caffeinate"
  local pid
  pid="$(read_pid caffeinate)"
  if [[ -n "${pid}" ]]; then
    echo "caffeinate: running pid=${pid}"
  fi
}

stop_service() {
  local service="$1"
  local pid
  local port

  clear_stale_pid "${service}"
  pid="$(read_pid "${service}")"
  if [[ -z "${pid}" ]]; then
    port="${BACKEND_PORT}"
    if [[ "${service}" == "frontend" ]]; then
      port="${FRONTEND_PORT}"
    fi
    if port_in_use "${port}"; then
      echo "${service} is not managed by this script, but port ${port} is in use"
      return 0
    fi
    echo "${service} already stopped"
    return 0
  fi

  echo "stopping ${service} (pid ${pid})"
  kill_process_tree "${pid}"

  rm -f "$(pid_file_for "${service}")"
}

stop_started_services() {
  local pids=()
  if [[ -f "$(started_marker_for frontend)" ]]; then
    stop_service "frontend" &
    pids+=("$!")
  fi
  if [[ -f "$(started_marker_for backend)" ]]; then
    stop_service "backend" &
    pids+=("$!")
  fi
  for pid in "${pids[@]}"; do
    wait "${pid}" || true
  done
  rm -f "$(started_marker_for frontend)" "$(started_marker_for backend)"
}

service_health() {
  local service="$1"
  if [[ "${service}" == "backend" ]]; then
    if curl --silent --fail --max-time 2 "http://127.0.0.1:${BACKEND_PORT}/health" >/dev/null 2>&1; then
      echo "healthy"
    else
      echo "unhealthy"
    fi
    return
  fi

  if curl --silent --fail --max-time 2 "http://127.0.0.1:${FRONTEND_PORT}" >/dev/null 2>&1; then
    echo "healthy"
  else
    echo "unhealthy"
  fi
}

status_service() {
  local service="$1"
  local port="$2"
  local pid

  clear_stale_pid "${service}"
  pid="$(read_pid "${service}")"
  if [[ -z "${pid}" ]]; then
    if port_in_use "${port}"; then
      echo "${service}: unmanaged port=${port} in-use"
      return
    fi
    echo "${service}: stopped"
    return
  fi

  echo "${service}: running pid=${pid} port=${port} health=$(service_health "${service}")"
}

tail_logs() {
  local target="${1:-all}"
  ensure_state_dirs
  touch "${BACKEND_LOG}" "${FRONTEND_LOG}"

  case "${target}" in
    backend)
      tail -n 50 -f "${BACKEND_LOG}"
      ;;
    frontend)
      tail -n 50 -f "${FRONTEND_LOG}"
      ;;
    all)
      tail -n 50 -f "${BACKEND_LOG}" "${FRONTEND_LOG}"
      ;;
    *)
      echo "unknown service: ${target}" >&2
      exit 1
      ;;
  esac
}

start_stack() {
  require_command "curl"
  require_command "lsof"
  require_command "npm"
  require_command "pgrep"
  require_command "uv"

  ensure_state_dirs
  rm -f "$(started_marker_for backend)" "$(started_marker_for frontend)"

  start_backend &
  local backend_pid=$!
  start_frontend &
  local frontend_pid=$!

  local backend_rc=0 frontend_rc=0
  wait "${backend_pid}" || backend_rc=$?
  wait "${frontend_pid}" || frontend_rc=$?

  if (( backend_rc != 0 || frontend_rc != 0 )); then
    stop_started_services
    return 1
  fi

  start_caffeinate
  status_stack
}

stop_stack() {
  stop_caffeinate &
  local cf=$!
  stop_service "frontend" &
  local fe=$!
  stop_service "backend" &
  local be=$!
  wait "${cf}" || true
  wait "${fe}" || true
  wait "${be}" || true
}

restart_stack() {
  local target="${1:-all}"
  case "${target}" in
    backend)
      stop_service "backend"
      start_backend
      status_stack
      ;;
    frontend)
      stop_service "frontend"
      start_frontend
      status_stack
      ;;
    all)
      stop_stack
      start_stack
      ;;
    *)
      echo "unknown service: ${target}" >&2
      exit 1
      ;;
  esac
}

status_stack() {
  status_service "backend" "${BACKEND_PORT}"
  status_service "frontend" "${FRONTEND_PORT}"
  status_caffeinate
}

main() {
  local command="${1:-}"
  load_env_file
  init_config
  case "${command}" in
    pwd)
      print_root_dir
      ;;
    start)
      start_stack
      ;;
    stop)
      stop_stack
      ;;
    restart)
      restart_stack "${2:-all}"
      ;;
    status)
      status_stack
      ;;
    logs)
      tail_logs "${2:-all}"
      ;;
    ""|-h|--help|help)
      usage
      ;;
    *)
      echo "unknown command: ${command}" >&2
      usage >&2
      return 1
      ;;
  esac
}

main "$@"
