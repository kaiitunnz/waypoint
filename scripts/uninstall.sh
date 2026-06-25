#!/usr/bin/env bash
set -euo pipefail

# Reverses scripts/install.sh. Safe to run via `curl | bash` or through
# `waypointctl uninstall`, which copies this script out of the checkout before
# running it so removing the checkout can't pull the rug.
#
# Default: stop the stack, strip the shell-profile block, remove the managed
# checkout, and uninstall the waypointctl tool — but keep your data.
# --purge: also delete the state dir and backend data dir.

# These markers must match scripts/install.sh.
WP_BEGIN="# >>> waypoint >>>"
WP_END="# <<< waypoint <<<"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Expand a leading ~ like config.py's Path.expanduser(), so a tilde in
# WAYPOINT_HOME/.env matches the resolution the backend used.
expand_tilde() {
    case "$1" in
        "~")   printf '%s' "${HOME}" ;;
        "~/"*) printf '%s' "${HOME}/${1#\~/}" ;;
        *)     printf '%s' "$1" ;;
    esac
}

# ── argument parsing ────────────────────────────────────────────────────────
HOME_DIR="${WAYPOINT_HOME:-${HOME}/.waypoint/app}"
PURGE=0
FORCE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --home)
            [[ $# -ge 2 ]] || die "--home requires a value"
            HOME_DIR="$2"
            shift 2
            ;;
        --purge)
            PURGE=1
            shift
            ;;
        --force)
            FORCE=1
            shift
            ;;
        *)
            die "unknown argument: $1 (supported: --home <dir>, --purge, --force)"
            ;;
    esac
done

# ── resolve paths ───────────────────────────────────────────────────────────
HOME_DIR="$(expand_tilde "${HOME_DIR}")"
STATE_DIR="$(expand_tilde "${WAYPOINTCTL_STATE_DIR:-${HOME}/.waypoint}")"

# Read a KEY=value from a dotenv file (last assignment wins, quotes stripped).
read_dotenv() {
    local key="$1" file="$2"
    [[ -f "${file}" ]] || return 0
    sed -n "s/^[[:space:]]*${key}=//p" "${file}" | tail -n1 \
        | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

# backend_data_dir mirrors config.py: WAYPOINT_STACK_BACKEND_DATA_DIR (from .env,
# else the environment), ~ expanded, relative paths under home, default in state.
DATA_RAW="$(read_dotenv WAYPOINT_STACK_BACKEND_DATA_DIR "${HOME_DIR}/.env")"
[[ -n "${DATA_RAW}" ]] || DATA_RAW="${WAYPOINT_STACK_BACKEND_DATA_DIR:-}"
DATA_RAW="$(expand_tilde "${DATA_RAW}")"
if [[ -z "${DATA_RAW}" ]]; then
    DATA_DIR="${STATE_DIR}/backend-data"
elif [[ "${DATA_RAW}" = /* ]]; then
    DATA_DIR="${DATA_RAW}"
else
    DATA_DIR="${HOME_DIR}/${DATA_RAW}"
fi

printf 'Uninstalling Waypoint at %s\n' "${HOME_DIR}"

# ── stop the running stack ──────────────────────────────────────────────────
if command -v waypointctl >/dev/null 2>&1; then
    printf 'Stopping the stack...\n'
    waypointctl --home "${HOME_DIR}" stop >/dev/null 2>&1 || true
    waypointctl daemon stop >/dev/null 2>&1 || true
fi

# ── strip the shell-profile block ───────────────────────────────────────────
strip_block() {
    local rc="$1"
    [[ -f "${rc}" ]] || return 0
    grep -qF "${WP_BEGIN}" "${rc}" 2>/dev/null || return 0
    local tmp
    tmp="$(mktemp)"
    # Drop the marker block inclusive, plus the blank line install.sh prepends
    # (buffer blank lines and discard them when they immediately precede a block).
    awk -v b="${WP_BEGIN}" -v e="${WP_END}" '
        $0==b {skip=1; blank=0; next}
        skip {if ($0==e) skip=0; next}
        /^$/ {blank++; next}
        {for (; blank>0; blank--) print ""; print}
        END {for (; blank>0; blank--) print ""}
    ' "${rc}" > "${tmp}"
    cat "${tmp}" > "${rc}"
    rm -f "${tmp}"
    printf 'Removed WAYPOINT_HOME from %s\n' "${rc}"
}

for rc in "${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.profile" \
          "${HOME}/.config/fish/config.fish"; do
    strip_block "${rc}"
done

# ── remove the managed checkout ─────────────────────────────────────────────
remove_checkout() {
    if [[ ! -d "${HOME_DIR}" ]]; then
        return 0
    fi
    local managed
    managed="$(git -C "${HOME_DIR}" config --get waypoint.managed 2>/dev/null || true)"
    if [[ "${managed}" != "true" && "${FORCE}" -ne 1 ]]; then
        printf 'warning: %s is not an installer-managed checkout; leaving it in place (use --force to remove it)\n' \
            "${HOME_DIR}" >&2
        return 0
    fi
    # Preserve a data dir that lives inside the checkout unless we are purging.
    if [[ "${PURGE}" -ne 1 && "${DATA_DIR}/" = "${HOME_DIR}/"* ]]; then
        printf 'warning: data dir %s is inside the checkout; leaving %s in place to preserve it (re-run with --purge to remove both)\n' \
            "${DATA_DIR}" "${HOME_DIR}" >&2
        return 0
    fi
    printf 'Removing checkout %s\n' "${HOME_DIR}"
    rm -rf "${HOME_DIR}"
}
remove_checkout

# ── purge data ──────────────────────────────────────────────────────────────
if [[ "${PURGE}" -eq 1 ]]; then
    for target in "${STATE_DIR}" "${DATA_DIR}"; do
        if [[ -e "${target}" ]]; then
            printf 'Removing %s\n' "${target}"
            rm -rf "${target}"
        fi
    done
fi

# ── uninstall the tool (last; it removes the command running this) ───────────
if command -v uv >/dev/null 2>&1; then
    printf 'Uninstalling waypointctl...\n'
    uv tool uninstall waypointctl >/dev/null 2>&1 || true
fi

printf '\nWaypoint uninstalled.\n'
if [[ "${PURGE}" -ne 1 ]]; then
    printf 'Kept data dir %s and state dir %s (re-run with --purge to remove them).\n' \
        "${DATA_DIR}" "${STATE_DIR}"
fi
