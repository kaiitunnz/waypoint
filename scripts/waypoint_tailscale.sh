#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_ROOT="${WAYPOINTCTL_STATE_DIR:-${HOME}/.waypoint}/tailscale"
BACKEND_PORT="${WAYPOINT_STACK_BACKEND_PORT:-8787}"
FRONTEND_PORT="${WAYPOINT_STACK_FRONTEND_PORT:-3000}"
HOST_ALIAS="${TS_HOST_ALIAS:-host.docker.internal}"

usage() {
  cat <<'EOF'
Usage: scripts/waypoint_tailscale.sh <up|down|status|logs> <profile>

Environment:
  WAYPOINTCTL_STATE_DIR              Root of Waypoint control-plane state
  WAYPOINT_STACK_BACKEND_PORT        Backend HTTP port to expose through Tailscale
  WAYPOINT_STACK_FRONTEND_PORT       Frontend HTTP port to expose through Tailscale
  WAYPOINT_TAILSCALE_READY_ATTEMPTS  Seconds to wait for BackendState=Running (default: 60)
  TS_AUTHKEY                         Required for `up`; read from the repo-root `.env`
  TS_HOSTNAME                        Optional node name; defaults to waypoint-<profile>
  TS_IMAGE                           Optional Tailscale image; defaults to tailscale/tailscale:latest

The helper reads the repo-root `.env` if present, matching waypointctl.
EOF
}

die() {
  echo "$1" >&2
  exit 1
}

require_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker is required for this helper."
}

slugify_profile() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]+/-/g; s/^[._-]+//; s/[._-]+$//'
}

profile_root() {
  printf '%s/%s' "${STATE_ROOT}" "$(slugify_profile "$1")"
}

container_name() {
  printf 'waypoint-tailscale-%s' "$(slugify_profile "$1")"
}

load_repo_env() {
  local env_file="${ROOT_DIR}/.env"
  [[ -f "${env_file}" ]] || return 0
  local line key value dq sq
  dq='"'
  sq="'"
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "${line}" || "${line:0:1}" == "#" ]] && continue
    [[ "${line}" == *"="* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ ${#value} -ge 2 && "${value:0:1}" == "${dq}" && "${value: -1}" == "${dq}" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ ${#value} -ge 2 && "${value:0:1}" == "${sq}" && "${value: -1}" == "${sq}" ]]; then
      value="${value:1:${#value}-2}"
    else
      value="${value#"${value%%[![:space:]]*}"}"
      value="${value%"${value##*[![:space:]]}"}"
    fi
    export "${key}=${value}"
  done < "${env_file}"
}

container_exists() {
  docker inspect "$1" >/dev/null 2>&1
}

container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || echo false)" == "true" ]]
}

ensure_state_dirs() {
  mkdir -p "${STATE_ROOT}" "$1"
}

run_container() {
  local name="$1"
  local node_name="$2"
  local profile_slug="$3"
  local state_dir="$4"
  local image="$5"
  docker run -d \
    --name "${name}" \
    --hostname "${node_name}" \
    --label "waypoint.role=tailscale" \
    --label "waypoint.profile=${profile_slug}" \
    --add-host "${HOST_ALIAS}:host-gateway" \
    -e TS_AUTHKEY="${TS_AUTHKEY}" \
    -e TS_HOSTNAME="${node_name}" \
    -e TS_STATE_DIR=/var/lib/tailscale \
    -v "${state_dir}:/var/lib/tailscale" \
    --cap-add=net_admin \
    --cap-add=net_raw \
    --restart unless-stopped \
    "${image}"
}

wait_until_ready() {
  local name="$1"
  local attempts="${2:-${WAYPOINT_TAILSCALE_READY_ATTEMPTS:-60}}"
  local i
  for ((i = 1; i <= attempts; i++)); do
    # `tailscale status --json` exits 0 as soon as the daemon answers,
    # including in NeedsLogin/Starting. Block on BackendState=Running so
    # we don't race `tailscale serve` against an unauthenticated node.
    if docker exec "${name}" tailscale status --json 2>/dev/null \
      | grep -q '"BackendState":[[:space:]]*"Running"'; then
      return 0
    fi
    sleep 1
  done
  return 1
}

rollback_container() {
  local name="$1"
  {
    echo "--- last 50 lines of docker logs for ${name} ---"
    docker logs --tail 50 "${name}" 2>&1 || true
    echo "--- end docker logs ---"
  } >&2
  docker rm -f "${name}" >/dev/null 2>&1 || true
}

configure_serves() {
  local name="$1"
  local frontend_port="$2"
  local backend_port="$3"
  docker exec "${name}" tailscale serve --bg --yes --http="${frontend_port}" "http://${HOST_ALIAS}:${frontend_port}" \
    && docker exec "${name}" tailscale serve --bg --yes --http="${backend_port}" "http://${HOST_ALIAS}:${backend_port}"
}

cmd_up() {
  local profile="$1"
  local slug node_name root state_dir name image started_now=0
  slug="$(slugify_profile "${profile}")"
  name="$(container_name "${profile}")"
  root="$(profile_root "${profile}")"
  state_dir="${root}"

  load_repo_env
  : "${TS_AUTHKEY:?TS_AUTHKEY is required in ${ROOT_DIR}/.env}"
  node_name="${TS_HOSTNAME:-waypoint-${slug}}"
  image="${TS_IMAGE:-tailscale/tailscale:latest}"

  require_docker
  ensure_state_dirs "${root}"

  if container_exists "${name}"; then
    if container_running "${name}"; then
      echo "tailscale container already running: ${name}"
    else
      docker start "${name}" >/dev/null
      started_now=1
    fi
  else
    run_container "${name}" "${node_name}" "${slug}" "${state_dir}" "${image}" >/dev/null
    started_now=1
  fi

  if ! wait_until_ready "${name}"; then
    [[ "${started_now}" -eq 1 ]] && rollback_container "${name}"
    die "Timed out waiting for ${name} to join the tailnet."
  fi

  if ! configure_serves "${name}" "${FRONTEND_PORT}" "${BACKEND_PORT}"; then
    [[ "${started_now}" -eq 1 ]] && rollback_container "${name}"
    die "Failed to configure Tailscale Serve for ${name}."
  fi

  echo "tailscale container ready: ${name}"
  echo "profile: ${slug}"
  echo "frontend: https://${node_name}:${FRONTEND_PORT}"
  echo "backend: https://${node_name}:${BACKEND_PORT}"
}

cmd_down() {
  local profile="$1"
  local name
  name="$(container_name "${profile}")"
  require_docker
  if ! container_exists "${name}"; then
    echo "tailscale container not found: ${name}"
    return 0
  fi
  # Tailnet identity lives in the mounted state dir, so removing the
  # container is safe — the next `up` rebuilds rather than starting a
  # potentially stale stopped container.
  docker rm -f "${name}" >/dev/null
  echo "tailscale container removed: ${name}"
}

cmd_status() {
  local profile="$1"
  local name
  name="$(container_name "${profile}")"
  require_docker
  if ! container_exists "${name}"; then
    echo "tailscale container missing: ${name}"
    return 0
  fi
  if container_running "${name}"; then
    echo "tailscale container running: ${name}"
    return 0
  fi
  echo "tailscale container stopped: ${name}"
}

cmd_logs() {
  local profile="$1"
  local name
  name="$(container_name "${profile}")"
  require_docker
  if ! container_exists "${name}"; then
    die "tailscale container not found: ${name}"
  fi
  docker logs -f --tail 50 "${name}"
}

main() {
  if [[ $# -lt 2 ]]; then
    usage
    exit 1
  fi

  local command="$1"
  local profile="$2"
  shift 2

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "unknown argument: $1"
        ;;
    esac
  done

  if [[ -z "$(slugify_profile "${profile}")" ]]; then
    die "invalid profile name: ${profile}"
  fi

  case "${command}" in
    up) cmd_up "${profile}" ;;
    down) cmd_down "${profile}" ;;
    status) cmd_status "${profile}" ;;
    logs) cmd_logs "${profile}" ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
