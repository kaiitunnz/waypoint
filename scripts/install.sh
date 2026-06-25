#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/kaiitunnz/waypoint"
WP_BEGIN="# >>> waypoint >>>"
WP_END="# <<< waypoint <<<"
INSTALL_DIR="${WAYPOINT_HOME:-${HOME}/.waypoint/app}"

die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$2"; }

gen_password() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 16
    elif [[ -r /dev/urandom ]]; then
        od -An -tx1 -N16 /dev/urandom | tr -d ' \n'
    else
        return 1
    fi
}

# ── argument parsing ────────────────────────────────────────────────────────
DEFAULT_BRANCH="main"
TARGET_REF="${WAYPOINT_VERSION:-}"
NIGHTLY=0
FORCE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ref)
            [[ $# -ge 2 ]] || die "--ref requires a value"
            TARGET_REF="$2"
            shift 2
            ;;
        --nightly)
            NIGHTLY=1
            shift
            ;;
        --force)
            FORCE=1
            shift
            ;;
        *)
            die "unknown argument: $1 (supported: --ref <tag>, --nightly, --force)"
            ;;
    esac
done

# ── resolve target ref ──────────────────────────────────────────────────────
if [[ "${NIGHTLY}" -eq 1 ]]; then
    [[ -z "${TARGET_REF}" ]] || die "--nightly cannot be combined with --ref/WAYPOINT_VERSION"
    TARGET_REF="${DEFAULT_BRANCH}"
    printf 'Installing the nightly build (tip of %s)\n' "${DEFAULT_BRANCH}"
elif [[ -z "${TARGET_REF}" ]]; then
    printf 'Fetching latest release tag...\n'
    TARGET_REF="$(
        curl -fsSL 'https://api.github.com/repos/kaiitunnz/waypoint/releases/latest' \
            | grep '"tag_name"' \
            | sed 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/'
    )"
    [[ -n "${TARGET_REF}" ]] || die "could not resolve latest release tag from GitHub"
fi

printf 'Installing Waypoint %s → %s\n' "${TARGET_REF}" "${INSTALL_DIR}"

# ── prerequisites ───────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    printf 'uv not found — installing...\n'
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck source=/dev/null
    [[ -f "${HOME}/.local/bin/env" ]] && . "${HOME}/.local/bin/env"
    export PATH="${HOME}/.local/bin:${PATH}"
fi
need uv  "uv installation failed; see https://docs.astral.sh/uv/getting-started/installation/"
need git "git is required — install via your package manager (e.g. apt install git or brew install git)"

if ! command -v node >/dev/null 2>&1; then
    die "Node.js >= 20 is required but not found — see https://nodejs.org"
fi
NODE_MAJOR="$(node --version | sed 's/v\([0-9]*\)\..*/\1/')"
if [[ -z "${NODE_MAJOR}" ]]; then
    die "could not determine Node.js version; ensure 'node' is on PATH"
fi
if [[ "${NODE_MAJOR}" -lt 20 ]]; then
    die "Node.js >= 20 is required (found $(node --version)) — see https://nodejs.org"
fi
need npm "npm is required — it should ship with Node.js"

# ── clone or update repo ────────────────────────────────────────────────────
# Branch refs track the remote tip (so nightly/--ref main actually advance);
# tags and SHAs detach. Either way we land in a detached HEAD at the target.
checkout_target() {
    local dir="$1" ref="$2"
    git -C "${dir}" fetch --force --tags origin
    if git -C "${dir}" rev-parse --verify --quiet "refs/remotes/origin/${ref}" >/dev/null; then
        git -C "${dir}" checkout --detach "origin/${ref}"
    else
        git -C "${dir}" checkout --detach "${ref}"
    fi
}

if [[ -d "${INSTALL_DIR}/.git" ]]; then
    # Only touch a checkout this installer created, and never one with local
    # work — so pointing WAYPOINT_HOME at a development clone can't clobber it.
    managed="$(git -C "${INSTALL_DIR}" config --get waypoint.managed 2>/dev/null || true)"
    if [[ "${managed}" != "true" && "${FORCE}" -ne 1 ]]; then
        die "refusing to update ${INSTALL_DIR}: not an installer-managed checkout (it looks like your own clone). Point WAYPOINT_HOME at a dedicated directory, or re-run with --force to repoint this one."
    fi
    # In a managed checkout, clear build-generated drift (start/restart rewrites
    # these tracked files) so it doesn't trip the dirty-tree guard below.
    if [[ "${managed}" = "true" ]]; then
        git -C "${INSTALL_DIR}" checkout -- frontend/next-env.d.ts frontend/tsconfig.json 2>/dev/null || true
    fi
    if [[ -n "$(git -C "${INSTALL_DIR}" status --porcelain)" ]]; then
        die "refusing to update ${INSTALL_DIR}: it has uncommitted changes. Commit or stash them first."
    fi
    [[ "${managed}" = "true" ]] || printf 'warning: repointing a non-managed checkout (--force)\n' >&2
    printf 'Updating %s to %s...\n' "${INSTALL_DIR}" "${TARGET_REF}"
    checkout_target "${INSTALL_DIR}" "${TARGET_REF}"
    git -C "${INSTALL_DIR}" config waypoint.managed true
else
    printf 'Cloning to %s...\n' "${INSTALL_DIR}"
    mkdir -p "$(dirname "${INSTALL_DIR}")"
    git clone --branch "${TARGET_REF}" "${REPO}" "${INSTALL_DIR}"
    git -C "${INSTALL_DIR}" config waypoint.managed true
fi

# ── seed config files (idempotent) ──────────────────────────────────────────
if [[ ! -f "${INSTALL_DIR}/backend/waypoint.yaml" ]]; then
    cp "${INSTALL_DIR}/backend/waypoint.example.yaml" \
       "${INSTALL_DIR}/backend/waypoint.yaml"
    printf 'Seeded backend/waypoint.yaml\n'
fi

if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
    cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
    chmod 600 "${INSTALL_DIR}/.env"
    RANDOM_PW="$(gen_password)" || die "no entropy source (openssl or /dev/urandom) to generate a password; set WAYPOINT_PASSWORD manually in ${INSTALL_DIR}/.env"
    awk -v pw="${RANDOM_PW}" \
        '/^# WAYPOINT_PASSWORD=/{print "WAYPOINT_PASSWORD=" pw; next} {print}' \
        "${INSTALL_DIR}/.env" > "${INSTALL_DIR}/.env.tmp"
    mv "${INSTALL_DIR}/.env.tmp" "${INSTALL_DIR}/.env"
    chmod 600 "${INSTALL_DIR}/.env"
    printf 'Seeded .env with a random WAYPOINT_PASSWORD\n'
fi

# ── install waypointctl ─────────────────────────────────────────────────────
printf 'Installing waypointctl...\n'
uv tool install --force "${INSTALL_DIR}/waypointctl"

# ── start stack ─────────────────────────────────────────────────────────────
printf 'Starting Waypoint...\n'
export WAYPOINT_HOME="${INSTALL_DIR}"
waypointctl start

# ── persist WAYPOINT_HOME in shell profiles ─────────────────────────────────
EXPORT_LINE="export WAYPOINT_HOME=\"${INSTALL_DIR}\""
FISH_LINE="set -gx WAYPOINT_HOME \"${INSTALL_DIR}\""

inject_export() {
    local rc_file="$1"
    local line="$2"
    grep -qF "${WP_BEGIN}" "${rc_file}" 2>/dev/null && return 0
    printf '\n%s\n%s\n%s\n' "${WP_BEGIN}" "${line}" "${WP_END}" >> "${rc_file}"
    printf 'Added WAYPOINT_HOME to %s\n' "${rc_file}"
}

inject_if_exists() {
    if [[ -f "$1" ]]; then
        inject_export "$1" "$2"
    fi
}

SHELL_NAME="$(basename "${SHELL:-bash}")"
case "${SHELL_NAME}" in
    zsh)  PRIMARY_RC="${HOME}/.zshrc" ;;
    fish) PRIMARY_RC="${HOME}/.config/fish/config.fish" ;;
    *)    PRIMARY_RC="${HOME}/.bashrc" ;;
esac

if [[ "${SHELL_NAME}" = "fish" ]]; then
    mkdir -p "$(dirname "${PRIMARY_RC}")"
    inject_export "${PRIMARY_RC}" "${FISH_LINE}"
else
    inject_export "${PRIMARY_RC}" "${EXPORT_LINE}"
fi

for extra_rc in "${HOME}/.bashrc" "${HOME}/.zshrc" "${HOME}/.profile"; do
    [[ "${extra_rc}" = "${PRIMARY_RC}" ]] && continue
    inject_if_exists "${extra_rc}" "${EXPORT_LINE}"
done

if [[ "${SHELL_NAME}" != "fish" ]]; then
    inject_if_exists "${HOME}/.config/fish/config.fish" "${FISH_LINE}"
fi

# ── done ────────────────────────────────────────────────────────────────────
printf '\nWaypoint %s installed to %s\n' "${TARGET_REF}" "${INSTALL_DIR}"
printf 'To load waypointctl in this shell:\n'
printf '  source %s\n' "${PRIMARY_RC}"
printf '  # or open a new terminal\n\n'
printf 'Then run: waypointctl status\n'
