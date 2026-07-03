#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${ROOT_DIR}/.agents/skills"

# Default skill installed for use by arbitrary coding sessions. The other repo
# skills (waypoint, waypointctl) target the personal assistant and are mirrored
# into its workspace already, so they are not installed globally by default.
DEFAULT_SKILLS=("waypoint-subagents" "waypoint-comms" "waypoint-workqueue" "waypoint-crew" "waypoint-worktree")

# Default destination skill root: the cross-agent global skills directory.
# Override with --skill-dir or WAYPOINT_SKILLS_DIR to target an agent-specific
# directory (e.g. ~/.claude/skills, ~/.codex/skills).
DEFAULT_DEST="${HOME}/.agents/skills"

usage() {
  cat <<'EOF'
Usage: scripts/install_skills.sh <install|uninstall|status> [options]

Install Waypoint coding-agent skills into per-agent global skill directories so
that any coding session — not just the personal assistant — can discover them.

Commands:
  install      Link (or copy) the selected skills into each destination.
  uninstall    Remove entries this installer created (symlinks into this repo).
  status       Report what is installed in each destination.

Options:
  --skill-dir <path>   Destination skill root. Repeatable. Overrides defaults.
  --skill <name>       Skill to act on (from .agents/skills). Repeatable.
  --all                Act on every skill under .agents/skills.
  --copy               Copy skills instead of symlinking (install only).
  -h, --help           Show this help.

Environment:
  WAYPOINT_SKILLS_DIR  Colon-separated destination roots, used when no
                       --skill-dir is given.

Defaults:
  skills        waypoint-subagents waypoint-comms waypoint-workqueue waypoint-crew waypoint-worktree
  destination   ~/.agents/skills

Symlink installs track this repo's copy of the skill. Copied installs are
detached snapshots; uninstall will not remove a copied or pre-existing
directory, only symlinks that point back into this repo.
EOF
}

die() {
  echo "error: $1" >&2
  exit 1
}

command="${1:-}"
case "${command}" in
  install | uninstall | status) shift ;;
  -h | --help | "")
    usage
    exit 0
    ;;
  *) die "unknown command: ${command} (expected install|uninstall|status)" ;;
esac

dests=()
skills=()
copy=0
all=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skill-dir)
      [[ $# -ge 2 ]] || die "--skill-dir needs a path"
      dests+=("$2")
      shift 2
      ;;
    --skill)
      [[ $# -ge 2 ]] || die "--skill needs a name"
      skills+=("$2")
      shift 2
      ;;
    --all)
      all=1
      shift
      ;;
    --copy)
      copy=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -d "${SOURCE_DIR}" ]] || die "missing skills source: ${SOURCE_DIR}"

# Resolve destinations: explicit --skill-dir wins, then WAYPOINT_SKILLS_DIR,
# then the built-in defaults.
if [[ ${#dests[@]} -eq 0 ]]; then
  if [[ -n "${WAYPOINT_SKILLS_DIR:-}" ]]; then
    IFS=':' read -r -a dests <<<"${WAYPOINT_SKILLS_DIR}"
  else
    dests=("${DEFAULT_DEST}")
  fi
fi

# Resolve skills: --all wins, then explicit --skill, then the default.
if [[ ${all} -eq 1 ]]; then
  skills=()
  for path in "${SOURCE_DIR}"/*/; do
    [[ -f "${path}SKILL.md" ]] && skills+=("$(basename "${path}")")
  done
  [[ ${#skills[@]} -gt 0 ]] || die "no skills found under ${SOURCE_DIR}"
elif [[ ${#skills[@]} -eq 0 ]]; then
  skills=("${DEFAULT_SKILLS[@]}")
fi

for skill in "${skills[@]}"; do
  [[ -f "${SOURCE_DIR}/${skill}/SKILL.md" ]] ||
    die "unknown skill: ${skill} (no ${SOURCE_DIR}/${skill}/SKILL.md)"
done

# True when ${1} is a symlink resolving into our source skills directory.
is_managed_link() {
  local entry="$1"
  [[ -L "${entry}" ]] || return 1
  local target
  target="$(readlink -f "${entry}" 2>/dev/null || true)"
  [[ -n "${target}" && "${target}" == "$(readlink -f "${SOURCE_DIR}")"/* ]]
}

do_install() {
  local dest skill src entry
  for dest in "${dests[@]}"; do
    mkdir -p "${dest}"
    for skill in "${skills[@]}"; do
      src="${SOURCE_DIR}/${skill}"
      entry="${dest}/${skill}"
      if [[ -e "${entry}" || -L "${entry}" ]]; then
        if is_managed_link "${entry}"; then
          rm -f "${entry}"
        elif [[ -L "${entry}" ]]; then
          die "${entry} is a symlink we do not own; remove it manually first"
        else
          die "${entry} already exists and was not created by this installer; refusing to overwrite"
        fi
      fi
      if [[ ${copy} -eq 1 ]]; then
        cp -R "${src}" "${entry}"
        echo "copied  ${skill} -> ${entry}"
      else
        ln -s "$(readlink -f "${src}")" "${entry}"
        echo "linked  ${skill} -> ${entry}"
      fi
    done
  done
}

do_uninstall() {
  local dest skill entry
  for dest in "${dests[@]}"; do
    for skill in "${skills[@]}"; do
      entry="${dest}/${skill}"
      if is_managed_link "${entry}"; then
        rm -f "${entry}"
        echo "removed ${entry}"
      elif [[ -e "${entry}" || -L "${entry}" ]]; then
        echo "skip    ${entry} (not a managed symlink; remove manually if intended)" >&2
      else
        echo "absent  ${entry}"
      fi
    done
  done
}

do_status() {
  local dest skill entry
  for dest in "${dests[@]}"; do
    for skill in "${skills[@]}"; do
      entry="${dest}/${skill}"
      if is_managed_link "${entry}"; then
        echo "linked  ${entry} -> $(readlink "${entry}")"
      elif [[ -L "${entry}" ]]; then
        echo "foreign ${entry} -> $(readlink "${entry}")"
      elif [[ -d "${entry}" ]]; then
        echo "copied  ${entry} (plain directory)"
      else
        echo "absent  ${entry}"
      fi
    done
  done
}

case "${command}" in
  install) do_install ;;
  uninstall) do_uninstall ;;
  status) do_status ;;
esac
