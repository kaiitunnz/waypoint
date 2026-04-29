#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="${ROOT_DIR}/backend"
FRONTEND_DIR="${ROOT_DIR}/frontend"
STATE_DIR="${ROOT_DIR}/tmp/dev-stack"
RUN_DIR="${STATE_DIR}/run"
LOG_DIR="${STATE_DIR}/logs"
ENV_FILE="${ROOT_DIR}/.env"

BACKEND_HOST=""
BACKEND_PORT=""
BACKEND_CONFIG=""
BACKEND_DATA_DIR=""
FRONTEND_PORT=""
START_TIMEOUT=""
UV_CACHE_DIR=""

BACKEND_LOG="${LOG_DIR}/backend.log"
FRONTEND_LOG="${LOG_DIR}/frontend.log"

BACKEND_STARTED_THIS_RUN=0
FRONTEND_STARTED_THIS_RUN=0

usage() {
  cat <<'EOF'
Usage: scripts/dev-stack.sh <command> [service]

Commands:
  start           Start backend and frontend in the background
  stop            Stop backend and frontend
  restart         Restart backend and frontend
  status          Show PID, port, and health for both services
  logs [service]  Tail logs for backend, frontend, or both

Environment overrides:
  WAYPOINT_STACK_BACKEND_HOST      Default: 0.0.0.0
  WAYPOINT_STACK_BACKEND_PORT      Default: 8787
  WAYPOINT_STACK_CONFIG            Default: backend/waypoint.yaml
  WAYPOINT_STACK_BACKEND_DATA_DIR  Default: tmp/dev-stack/backend-data
  WAYPOINT_STACK_FRONTEND_PORT     Default: 3000
  WAYPOINT_STACK_START_TIMEOUT     Default: 30
  WAYPOINT_STACK_UV_CACHE_DIR      Default: tmp/dev-stack/uv-cache

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

init_config() {
  BACKEND_HOST="${WAYPOINT_STACK_BACKEND_HOST:-0.0.0.0}"
  BACKEND_PORT="${WAYPOINT_STACK_BACKEND_PORT:-8787}"
  BACKEND_CONFIG="${WAYPOINT_STACK_CONFIG:-${BACKEND_DIR}/waypoint.yaml}"
  BACKEND_DATA_DIR="${WAYPOINT_STACK_BACKEND_DATA_DIR:-${STATE_DIR}/backend-data}"
  FRONTEND_PORT="${WAYPOINT_STACK_FRONTEND_PORT:-3000}"
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
    exit 1
  fi

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

  BACKEND_STARTED_THIS_RUN=1

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
    exit 1
  fi

  : >"${FRONTEND_LOG}"
  echo "building frontend"
  (
    cd "${FRONTEND_DIR}"
    env PORT="${FRONTEND_PORT}" npm run build >>"${FRONTEND_LOG}" 2>&1
  )

  echo "starting frontend on 0.0.0.0:${FRONTEND_PORT}"
  (
    cd "${FRONTEND_DIR}"
    nohup env PORT="${FRONTEND_PORT}" npm run start >>"${FRONTEND_LOG}" 2>&1 &
    echo $! >"$(pid_file_for frontend)"
  )

  FRONTEND_STARTED_THIS_RUN=1

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
  kill "${pid}" >/dev/null 2>&1 || true
  if ! wait_for_exit "${pid}"; then
    kill -9 "${pid}" >/dev/null 2>&1 || true
  fi

  rm -f "$(pid_file_for "${service}")"
}

stop_started_services() {
  if (( FRONTEND_STARTED_THIS_RUN == 1 )); then
    stop_service "frontend"
  fi
  if (( BACKEND_STARTED_THIS_RUN == 1 )); then
    stop_service "backend"
  fi
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
  require_command "uv"

  start_backend
  start_frontend
  status_stack
}

stop_stack() {
  stop_service "frontend"
  stop_service "backend"
}

restart_stack() {
  stop_stack
  start_stack
}

status_stack() {
  status_service "backend" "${BACKEND_PORT}"
  status_service "frontend" "${FRONTEND_PORT}"
}

main() {
  local command="${1:-}"
  load_env_file
  init_config
  case "${command}" in
    start)
      trap stop_started_services ERR
      start_stack
      ;;
    stop)
      stop_stack
      ;;
    restart)
      trap stop_started_services ERR
      restart_stack
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
      exit 1
      ;;
  esac
}

main "$@"
