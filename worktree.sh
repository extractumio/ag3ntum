#!/usr/bin/env bash
# =============================================================================
# Ag3ntum Worktree Manager
# =============================================================================
# Manages git worktrees for running multiple Ag3ntum instances simultaneously.
# Each worktree gets isolated Docker containers, ports, data, and config.
#
# Worktrees are created as sibling directories to Project/:
#   Project/               (main, slot 0)
#   Project_wt_<name>/     (worktree instances, slots 1-9)
#
# Port allocation (slot × 10 offset):
#   Slot 0 (main):  API=40080  Web=50080  Redis=46379
#   Slot 1:         API=40090  Web=50090  Redis=46389
#   Slot 2:         API=40100  Web=50100  Redis=46399
#   ...
#
# Usage:
#   ./worktree.sh create <branch> [--name <name>] [--slot <N>]
#   ./worktree.sh destroy <name>
#   ./worktree.sh list
#   ./worktree.sh status [<name>]
#   ./worktree.sh help
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "${SCRIPT_DIR}")"

# Worktree directory prefix (sibling to Project/)
WT_PREFIX="Project_wt_"

# Port allocation base values
API_PORT_BASE=40080
WEB_PORT_BASE=50080
REDIS_PORT_BASE=46379
PORT_BLOCK_SIZE=10
MAX_SLOT=9

# =============================================================================
# Helpers
# =============================================================================

die() {
  echo "Error: $1" >&2
  exit 1
}

validate_branch() {
  local branch="$1"
  git -C "${SCRIPT_DIR}" rev-parse --verify "${branch}" >/dev/null 2>&1 \
    || die "Branch not found: ${branch}"
}

validate_name() {
  local name="$1"
  [[ "${name}" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] \
    || die "Invalid name: ${name} (use alphanumeric, hyphens, underscores)"
}

validate_slot() {
  local slot="$1"
  [[ "${slot}" =~ ^[0-9]$ ]] \
    || die "Invalid slot: ${slot} (must be 0-9)"
}

# Read API port from a directory's .env to determine its slot
read_slot_from_env() {
  local dir="$1"
  local env_file="${dir}/.env"
  [[ -f "${env_file}" ]] || return 1
  local port
  port=$(grep '^AG3NTUM_API_PORT=' "${env_file}" 2>/dev/null | cut -d= -f2 || true)
  if [[ -n "${port}" ]]; then
    echo $(( (port - API_PORT_BASE) / PORT_BLOCK_SIZE ))
  else
    return 1
  fi
}

# Find all used slots by scanning .env files
get_used_slots() {
  local slots=()

  # Check main worktree
  local main_slot
  if main_slot=$(read_slot_from_env "${SCRIPT_DIR}"); then
    slots+=("${main_slot}")
  fi

  # Check worktree instances
  for dir in "${PARENT_DIR}"/${WT_PREFIX}*/; do
    [[ -d "${dir}" ]] || continue
    local slot
    if slot=$(read_slot_from_env "${dir}"); then
      slots+=("${slot}")
    fi
  done

  # Print space-separated (or empty)
  echo "${slots[*]+"${slots[*]}"}"
}

# Find next free slot (1-9; slot 0 is reserved for main)
allocate_slot() {
  local used
  used="$(get_used_slots)"

  for s in $(seq 1 ${MAX_SLOT}); do
    local in_use=0
    for u in ${used}; do
      if [[ "${u}" -eq "${s}" ]]; then
        in_use=1
        break
      fi
    done
    if [[ "${in_use}" -eq 0 ]]; then
      echo "${s}"
      return
    fi
  done

  die "No free slots available (max: ${MAX_SLOT}). Destroy an existing worktree first."
}

# Check that a specific slot is not already in use
check_slot_free() {
  local slot="$1"
  local used
  used="$(get_used_slots)"

  for u in ${used}; do
    if [[ "${u}" -eq "${slot}" ]]; then
      # Find which directory owns it for a helpful error
      for dir in "${SCRIPT_DIR}" "${PARENT_DIR}"/${WT_PREFIX}*/; do
        [[ -d "${dir}" ]] || continue
        local dir_slot
        if dir_slot=$(read_slot_from_env "${dir}") && [[ "${dir_slot}" -eq "${slot}" ]]; then
          die "Slot ${slot} is already used by $(basename "${dir}")"
        fi
      done
      die "Slot ${slot} is already in use"
    fi
  done
}

# Check if a host port is free
check_port_free() {
  local port="$1"
  local label="${2:-Port}"
  if lsof -ti ":${port}" >/dev/null 2>&1; then
    die "${label} port ${port} is already in use. Check with: lsof -ti :${port}"
  fi
}

# Copy config files from main worktree to new worktree
copy_configs() {
  local target_dir="$1"
  local config_dir="${target_dir}/config"

  echo "Copying configuration from main worktree..."

  # secrets.yaml
  if [[ -f "${SCRIPT_DIR}/config/secrets.yaml" ]]; then
    cp "${SCRIPT_DIR}/config/secrets.yaml" "${config_dir}/secrets.yaml"
    echo "  Copied: secrets.yaml"
  else
    echo "  Warning: secrets.yaml not found in main worktree — copy manually"
  fi

  # api.yaml
  if [[ -f "${SCRIPT_DIR}/config/api.yaml" ]]; then
    cp "${SCRIPT_DIR}/config/api.yaml" "${config_dir}/api.yaml"
    echo "  Copied: api.yaml"
  else
    echo "  Warning: api.yaml not found in main worktree — copy from api.yaml.example"
  fi

  # external-mounts.yaml (if exists)
  if [[ -f "${SCRIPT_DIR}/config/external-mounts.yaml" ]]; then
    cp "${SCRIPT_DIR}/config/external-mounts.yaml" "${config_dir}/external-mounts.yaml"
    echo "  Copied: external-mounts.yaml"
  fi
}

# Patch api.yaml with instance-specific external_port values
patch_api_yaml() {
  local target_dir="$1"
  local api_port="$2"
  local web_port="$3"
  local config="${target_dir}/config/api.yaml"

  [[ -f "${config}" ]] || return 0

  # Patch external_port values using awk (portable across macOS/Linux)
  local tmp
  tmp="$(mktemp)"
  awk -v api_port="${api_port}" -v web_port="${web_port}" '
    /^api:/ { section="api" }
    /^web:/ { section="web" }
    /^[a-z]/ && !/^api:/ && !/^web:/ { section="" }
    section=="api" && /external_port:/ { sub(/external_port:.*/, "external_port: " api_port); print; next }
    section=="web" && /external_port:/ { sub(/external_port:.*/, "external_port: " web_port); print; next }
    { print }
  ' "${config}" > "${tmp}" && mv "${tmp}" "${config}"

  echo "  Patched: api.yaml (API=${api_port}, Web=${web_port})"
}

# =============================================================================
# Commands
# =============================================================================

cmd_create() {
  if [[ $# -lt 1 ]]; then
    die "Usage: ./worktree.sh create <branch> [--name <name>] [--slot <N>]"
  fi

  local branch="$1"
  local name=""
  local slot=""

  # Parse optional args
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --name)
        [[ $# -ge 2 ]] || die "--name requires a value"
        name="$2"; shift 2 ;;
      --slot)
        [[ $# -ge 2 ]] || die "--slot requires a value"
        slot="$2"; shift 2 ;;
      *)
        die "Unknown option: $1" ;;
    esac
  done

  # Default name from branch (sanitize: replace / with -)
  name="${name:-$(echo "${branch}" | tr '/' '-')}"

  local worktree_dir="${PARENT_DIR}/${WT_PREFIX}${name}"

  # Validate inputs
  validate_branch "${branch}"
  validate_name "${name}"
  [[ -d "${worktree_dir}" ]] && die "Directory already exists: ${worktree_dir}"

  # Allocate slot
  if [[ -z "${slot}" ]]; then
    slot=$(allocate_slot)
  fi
  validate_slot "${slot}"
  check_slot_free "${slot}"

  # Calculate ports
  local api_port=$((API_PORT_BASE + slot * PORT_BLOCK_SIZE))
  local web_port=$((WEB_PORT_BASE + slot * PORT_BLOCK_SIZE))
  local redis_port=$((REDIS_PORT_BASE + slot * PORT_BLOCK_SIZE))

  # Check ports are available on host
  check_port_free "${api_port}" "API"
  check_port_free "${web_port}" "Web"
  check_port_free "${redis_port}" "Redis"

  # Create git worktree
  echo "Creating worktree: ${worktree_dir}"
  echo "  Branch: ${branch}, Slot: ${slot}"
  git -C "${SCRIPT_DIR}" worktree add "${worktree_dir}" "${branch}"

  # Copy configs from main
  copy_configs "${worktree_dir}"

  # Patch api.yaml with instance ports
  patch_api_yaml "${worktree_dir}" "${api_port}" "${web_port}"

  # Write .env
  local project_name="${WT_PREFIX}${name}"
  local image_tag="latest"
  # Inherit image tag from main if available
  if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    image_tag=$(grep '^AG3NTUM_IMAGE_TAG=' "${SCRIPT_DIR}/.env" | cut -d= -f2 || echo "latest")
  fi

  cat > "${worktree_dir}/.env" <<EOF
AG3NTUM_IMAGE_TAG=${image_tag}
AG3NTUM_API_PORT=${api_port}
AG3NTUM_WEB_PORT=${web_port}
AG3NTUM_REDIS_PORT=${redis_port}
COMPOSE_PROJECT_NAME=${project_name}
EOF

  # Create data directories (not tracked by git, needed for bind mounts)
  mkdir -p "${worktree_dir}"/{data,logs,users}

  echo ""
  echo "=== Worktree created ==="
  echo "  Directory:    ${worktree_dir}"
  echo "  Branch:       ${branch}"
  echo "  Slot:         ${slot}"
  echo "  API port:     ${api_port}"
  echo "  Web port:     ${web_port}"
  echo "  Redis port:   ${redis_port}"
  echo "  Project name: ${project_name}"
  echo ""
  echo "Next steps:"
  echo "  cd ${worktree_dir}"
  echo "  ./run.sh build           # Build and start this instance"
  echo ""
  echo "After build:"
  echo "  Web UI: http://localhost:${web_port}"
  echo "  API:    http://localhost:${api_port}"
}

cmd_destroy() {
  if [[ $# -lt 1 ]]; then
    die "Usage: ./worktree.sh destroy <name>"
  fi

  local name="$1"
  local worktree_dir="${PARENT_DIR}/${WT_PREFIX}${name}"

  [[ -d "${worktree_dir}" ]] || die "Worktree not found: ${worktree_dir}"

  echo "Destroying instance: ${WT_PREFIX}${name}"

  # Stop Docker stack (scoped by COMPOSE_PROJECT_NAME in .env)
  if [[ -f "${worktree_dir}/.env" ]]; then
    echo "Stopping Docker stack..."
    (cd "${worktree_dir}" && docker compose down --remove-orphans --timeout 10 2>/dev/null) || true

    # Remove project-specific volumes and networks
    local project_name
    project_name=$(grep '^COMPOSE_PROJECT_NAME=' "${worktree_dir}/.env" | cut -d= -f2 || true)
    if [[ -n "${project_name}" ]]; then
      echo "Removing project volumes and networks..."
      docker volume ls --filter "name=${project_name}_" -q 2>/dev/null | xargs -r docker volume rm 2>/dev/null || true
      docker network ls --filter "name=${project_name}_" -q 2>/dev/null | xargs -r docker network rm 2>/dev/null || true
    fi
  fi

  # Remove git worktree
  echo "Removing git worktree..."
  git -C "${SCRIPT_DIR}" worktree remove "${worktree_dir}" --force 2>/dev/null || {
    echo "Warning: git worktree remove failed. Removing directory manually."
    rm -rf "${worktree_dir}"
    git -C "${SCRIPT_DIR}" worktree prune
  }

  echo ""
  echo "=== Instance destroyed: ${WT_PREFIX}${name} ==="
}

cmd_list() {
  printf "%-30s %-25s %-6s %-7s %-7s %-7s %s\n" \
    "INSTANCE" "BRANCH" "SLOT" "API" "WEB" "REDIS" "STATUS"
  printf "%s\n" "──────────────────────────────────────────────────────────────────────────────────────────────────────────"

  # Show main instance
  _show_instance_row "${SCRIPT_DIR}" "(main/Project)"

  # Show worktree instances
  for dir in "${PARENT_DIR}"/${WT_PREFIX}*/; do
    [[ -d "${dir}" ]] || continue
    local name
    name="$(basename "${dir}")"
    _show_instance_row "${dir}" "${name}"
  done
}

_show_instance_row() {
  local dir="$1"
  local label="$2"

  # Read .env values
  local api_port="" web_port="" redis_port="" project_name=""
  if [[ -f "${dir}/.env" ]]; then
    api_port=$(grep '^AG3NTUM_API_PORT=' "${dir}/.env" | cut -d= -f2 || true)
    web_port=$(grep '^AG3NTUM_WEB_PORT=' "${dir}/.env" | cut -d= -f2 || true)
    redis_port=$(grep '^AG3NTUM_REDIS_PORT=' "${dir}/.env" | cut -d= -f2 || true)
    project_name=$(grep '^COMPOSE_PROJECT_NAME=' "${dir}/.env" | cut -d= -f2 || true)
  fi

  # Get branch from git
  local branch=""
  branch=$(git -C "${dir}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "???")

  # Derive slot from port
  local slot="?"
  if [[ -n "${api_port}" ]]; then
    slot=$(( (api_port - API_PORT_BASE) / PORT_BLOCK_SIZE ))
  fi

  # Check Docker status
  local status="stopped"
  if [[ -n "${project_name}" ]]; then
    local running
    running=$(cd "${dir}" && docker compose ps --status running --services 2>/dev/null || true)
    if echo "${running}" | grep -q "ag3ntum-api"; then
      status="running"
    fi
  elif [[ -f "${dir}/.env" ]]; then
    status="not built"
  else
    status="no .env"
  fi

  printf "%-30s %-25s %-6s %-7s %-7s %-7s %s\n" \
    "${label}" "${branch}" "${slot}" "${api_port:-?}" "${web_port:-?}" "${redis_port:-?}" "${status}"
}

cmd_status() {
  local name="${1:-}"

  if [[ -z "${name}" ]]; then
    # Show status of all instances
    cmd_list
    return
  fi

  local worktree_dir="${PARENT_DIR}/${WT_PREFIX}${name}"
  [[ -d "${worktree_dir}" ]] || die "Worktree not found: ${worktree_dir}"

  echo "=== Instance: ${WT_PREFIX}${name} ==="
  echo ""

  # .env info
  if [[ -f "${worktree_dir}/.env" ]]; then
    echo "Configuration (.env):"
    while IFS= read -r line; do
      echo "  ${line}"
    done < "${worktree_dir}/.env"
    echo ""
  fi

  # Git info
  local branch
  branch=$(git -C "${worktree_dir}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "???")
  local commit
  commit=$(git -C "${worktree_dir}" rev-parse --short HEAD 2>/dev/null || echo "???")
  echo "Git:"
  echo "  Branch: ${branch}"
  echo "  Commit: ${commit}"
  echo "  Path:   ${worktree_dir}"
  echo ""

  # Docker status
  local project_name
  project_name=$(grep '^COMPOSE_PROJECT_NAME=' "${worktree_dir}/.env" 2>/dev/null | cut -d= -f2 || true)
  if [[ -n "${project_name}" ]]; then
    echo "Docker (${project_name}):"
    (cd "${worktree_dir}" && docker compose ps 2>/dev/null) || echo "  Not running"
  fi
  echo ""

  # Disk usage
  echo "Disk usage:"
  du -sh "${worktree_dir}/data" "${worktree_dir}/logs" "${worktree_dir}/users" 2>/dev/null || echo "  (no data directories)"
}

cmd_help() {
  cat <<'EOF'
Ag3ntum Worktree Manager — Run multiple instances simultaneously

Usage: ./worktree.sh <command> [options]

Commands:
  create <branch> [--name N] [--slot S]   Create a new worktree instance
  destroy <name>                           Stop Docker stack and remove worktree
  list                                     List all worktree instances with status
  status [<name>]                          Show detailed status of an instance
  help                                     Show this help message

Port Allocation (slot × 10 offset):
  Slot 0 (main):  API=40080  Web=50080  Redis=46379
  Slot 1:         API=40090  Web=50090  Redis=46389
  Slot 2:         API=40100  Web=50100  Redis=46399
  Slot 3:         API=40110  Web=50110  Redis=46409
  Slot 4:         API=40120  Web=50120  Redis=46419
  ...

Worktree Location:
  Worktrees are created as siblings to the main Project/ directory:
    Project/                (main, slot 0)
    Project_wt_<name>/      (worktree instances)

Examples:
  # Create worktree for a feature branch (auto-assigns slot)
  ./worktree.sh create feature/auth

  # Create with explicit name and slot
  ./worktree.sh create feature/auth --name auth --slot 2

  # List all instances
  ./worktree.sh list

  # Show detailed status
  ./worktree.sh status auth

  # Destroy an instance (stops Docker, removes worktree)
  ./worktree.sh destroy auth

Workflow:
  1. ./worktree.sh create feature/my-feature
  2. cd ../Project_wt_feature-my-feature
  3. ./run.sh build           # Build and start isolated Docker stack
  4. ./run.sh test --quick    # Run tests in this instance
  5. cd ../Project            # Back to main
  6. ./worktree.sh list       # See all instances
  7. ./worktree.sh destroy feature-my-feature  # Clean up when done

See also:
  /create_worktree    Claude Code command for agentic automation
EOF
}

# =============================================================================
# Main
# =============================================================================

case "${1:-help}" in
  create)
    shift
    cmd_create "$@"
    ;;
  destroy)
    shift
    cmd_destroy "$@"
    ;;
  list)
    cmd_list
    ;;
  status)
    shift
    cmd_status "${1:-}"
    ;;
  help|--help|-h)
    cmd_help
    ;;
  *)
    die "Unknown command: $1. Run './worktree.sh help' for usage."
    ;;
esac
