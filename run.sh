#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# Arrays for mount options (format: "path:name" or "path:name:mode" for dynamic)
MOUNTS_RW=()
MOUNTS_RO=()
MOUNTS_USER_RW=()
MOUNTS_USER_RO=()
MOUNTS_DYNAMIC=()  # Dynamic mount bases (format: "path:name:mode")
MOUNTS_ORIGINAL=() # Original-path mounts (format: "path:encoded:mode")

# Track used mount names to detect duplicates (space-separated string for Bash 3 compat)
USED_MOUNT_NAMES=""

# Configuration
IMAGE_PREFIX="ag3ntum"  # Image name prefix
CONTAINER_UID="45045"   # UID of ag3ntum_api user inside container

# Config file registry: "relative_path:tier"
# REQUIRED_SECRET = fail with instructions if missing (contains credentials)
# REQUIRED_SAFE   = auto-create from .example template if missing
CONFIG_REGISTRY=(
  "config/secrets.yaml:REQUIRED_SECRET"
  "config/agent.yaml:REQUIRED_SAFE"
  "config/api.yaml:REQUIRED_SAFE"
  "config/external-mounts.yaml:REQUIRED_SAFE"
  "config/llm-api-proxy.yaml:REQUIRED_SAFE"
)

# Reserved mount names that cannot be used
RESERVED_NAMES=("persistent" "ro" "rw" "external" "dynamic")

# Safely remove a file that may be owned by container UID (from a previous build).
# Uses Docker to remove files that the host user cannot — no sudo needed.
function safe_remove_file() {
  local file="$1"
  [[ ! -e "${file}" ]] && return 0

  # Try normal rm first (covers macOS, Windows, and Linux files owned by current user)
  if rm -f "${file}" 2>/dev/null; then
    return 0
  fi

  # Fall back to Docker for container-owned files (no sudo needed)
  local dir
  dir="$(dirname "${file}")"
  local base
  base="$(basename "${file}")"
  docker run --rm -v "$(pwd)/${dir}:/work" alpine rm -f "/work/${base}" 2>/dev/null || {
    echo "Warning: Cannot remove ${file} (owned by container UID ${CONTAINER_UID}). Continuing."
    return 0
  }
}

# Directories that container needs to WRITE to (ownership managed by container entrypoint)
# Note: node_modules uses a named Docker volume at /app/node_modules (outside /src:ro mount)
WRITABLE_DIRS=("logs" "data" "users")

# Directories that container only READS from (just need to exist)
READABLE_DIRS=("config" "src" "prompts" "skills" "tools" "tests" "auto-generated")

function show_usage() {
  cat <<EOF
Usage: ./run.sh <command> [OPTIONS]

Commands:
  setup              Install dev tools (Python venv + Node deps + pre-commit hooks)
  build              Build and deploy the containers
  cleanup            Stop containers and remove images (full cleanup)
  restart            Restart containers to reload code (preserves data)
  rebuild            Full cleanup + build (equivalent to: cleanup && build)
  test               Run tests inside the Docker container
  lint               Run linters (flake8, bandit, mypy, eslint, tsc, structural)
  audit              Run dependency vulnerability scan (pip-audit)
  shell              Open a shell inside the API container
  create-user        Create a new user account (uses AG3NTUM_UID_MODE setting)
  delete-user        Delete a user account
  cleanup-test-users Remove test users created during testing

UID Security Modes:
  AG3NTUM_UID_MODE=isolated  (default) UIDs 50000-60000, multi-tenant safe
  AG3NTUM_UID_MODE=direct    UIDs map to host (1000-65533), dev/single-tenant
  See docs/UID-SECURITY.md for details

Options:
  --dev                 Development mode (Vite dev server with HMR on separate port)
  --mount-rw=PATH:NAME  Mount host PATH as read-write (accessible at ./external/rw/NAME)
  --mount-rw=PATH       Mount host PATH as read-write (name defaults to basename)
  --mount-ro=PATH:NAME  Mount host PATH as read-only (accessible at ./external/ro/NAME)
  --mount-ro=PATH       Mount host PATH as read-only (name defaults to basename)
  --no-cache            Force rebuild without Docker cache (for build/rebuild)
  --help                Show this help message

Deployment Modes:
  prod (default)  Web container serves pre-built static bundle (fast startup)
  dev (--dev)     Web container runs Vite dev server with HMR (hot-reload)

Test Options (for 'test' command):
  (no args)               Run ALL tests (backend + security + E2E + UI)
  --quick                 Run only quick tests (exclude E2E and slow tests)
  --backend               Run only backend tests (Python/pytest)
  --ui                    Run only UI tests (React/vitest)
  --subset <names>        Run specific backend tests by name (comma-separated)
                          Examples: "auth", "sessions,streaming", "ask_user_question"

External Mount Configuration:
  Mounts can be configured via:
  1. CLI arguments (--mount-ro, --mount-rw) - highest priority
  2. YAML config file (config/external-mounts.yaml) - for persistent config

  To use YAML config:
    cp config/external-mounts.yaml.example config/external-mounts.yaml
    # Edit the file with your mounts
    ./run.sh build

External Mount Examples (CLI):
  # Mount Downloads folder as read-only, accessible at ./external/ro/downloads/
  ./run.sh build --mount-ro=/Users/greg/Downloads:downloads

  # Mount projects folder as read-write, accessible at ./external/rw/projects/
  ./run.sh build --mount-rw=/home/user/projects:projects

  # Multiple mounts with custom names
  ./run.sh build \\
    --mount-ro=/data/datasets:ml-data \\
    --mount-rw=/home/user/code:workspace

  # Auto-named mounts (uses basename of path)
  ./run.sh build --mount-ro=/Users/greg/Downloads  # -> ./external/ro/Downloads/

Mount Structure in Agent Sessions:
  /workspace/
  ├── external/
  │   ├── ro/           # Read-only mounts (agent cannot write)
  │   │   └── {name}/   # Your mounted folders
  │   ├── rw/           # Read-write mounts (agent can modify)
  │   │   └── {name}/   # Your mounted folders
  │   └── persistent/   # Per-user storage (survives across sessions)
  └── (session files)

General Examples:
  ./run.sh build
  ./run.sh build --no-cache
  ./run.sh cleanup
  ./run.sh restart
  ./run.sh rebuild --no-cache
  ./run.sh test                          # Run ALL tests (backend + UI)
  ./run.sh test --quick                  # Run quick tests only (no E2E/slow)
  ./run.sh test --backend                # Run backend tests only
  ./run.sh test --ui                     # Run UI/React tests only
  ./run.sh test --subset auth            # Run auth tests only
  ./run.sh test --subset sessions,auth   # Run sessions and auth tests
  ./run.sh shell                         # Open shell in container

Multi-Instance (Worktrees):
  ./worktree.sh create <branch>        # Create worktree with isolated Docker stack
  ./worktree.sh list                   # List all instances with ports and status
  ./worktree.sh destroy <name>         # Stop stack and remove worktree
  See: ./worktree.sh help

CLI Hints:
  View logs:     docker compose logs -f ag3ntum-api
  API health:    curl http://localhost:40080/api/v1/health
  Redis CLI:     docker compose exec redis redis-cli
  Shell:         docker compose exec ag3ntum-api bash
  Stop all:      docker compose down
EOF
}

# Setup directories — just ensure they exist.
# Permissions are managed by the container entrypoint (runs as root before dropping to 45045).
function setup_directories() {
  echo "=== Setting up directories ==="

  for dir in "${WRITABLE_DIRS[@]}" "${READABLE_DIRS[@]}"; do
    if [[ ! -d "${dir}" ]]; then
      echo "  Creating ${dir}/"
      mkdir -p "${dir}"
    fi
  done

  echo "  Directories ready (permissions managed by container entrypoint)"
}

# Validate config files from CONFIG_REGISTRY.
# Auto-creates REQUIRED_SAFE configs from .example templates.
# Fails with instructions for missing REQUIRED_SECRET configs.
function validate_and_provision_configs() {
  local missing_secrets=()

  for entry in "${CONFIG_REGISTRY[@]}"; do
    local cfg="${entry%%:*}"
    local tier="${entry##*:}"

    # Already exists — nothing to do
    if [[ -f "${cfg}" ]]; then
      continue
    fi

    if [[ "${tier}" == "REQUIRED_SAFE" ]]; then
      # Auto-create from .example template
      if [[ "${cfg}" == "config/external-mounts.yaml" ]]; then
        # Special case: .example contains sample paths that fail mount validation.
        # Create a minimal empty config instead.
        cat > "${cfg}" <<'EXTMOUNTS'
# External mounts configuration
# See external-mounts.yaml.example for documentation and examples.
original_paths:
  ro: []
  rw: []
EXTMOUNTS
        echo "INFO: Created ${cfg} (minimal empty config)"
      elif [[ -f "${cfg}.example" ]]; then
        cp "${cfg}.example" "${cfg}"
        echo "INFO: Created ${cfg} from ${cfg}.example — review and adjust as needed"
      else
        echo "WARNING: ${cfg} is missing and no .example template found"
      fi
    elif [[ "${tier}" == "REQUIRED_SECRET" ]]; then
      missing_secrets+=("${cfg}")
    fi
  done

  if [[ ${#missing_secrets[@]} -gt 0 ]]; then
    echo ""
    echo "ERROR: Required secret configuration files are missing:"
    for cfg in "${missing_secrets[@]}"; do
      echo "  - ${cfg}"
      if [[ -f "${cfg}.example" ]]; then
        echo "    Create from example: cp ${cfg}.example ${cfg}"
      fi
    done
    echo ""
    echo "These files contain credentials and cannot be auto-generated."
    echo "Run install.sh for guided setup, or create them manually from .example files."
    exit 1
  fi
}

# Validate and process a mount specification
# Usage: validate_mount "path" "name" "mode"
# Returns: validated "real_path:safe_name" or exits on error
function validate_mount() {
  local path="$1"
  local name="$2"
  local mode="$3"  # "ro" or "rw"

  # Check path exists
  if [[ ! -e "$path" ]]; then
    echo "ERROR: Mount path does not exist: $path" >&2
    exit 1
  fi

  # Resolve symlinks and get real path
  local real_path
  real_path="$(cd "$path" 2>/dev/null && pwd)" || {
    # If cd fails, try realpath (for files)
    real_path="$(realpath "$path" 2>/dev/null)" || {
      echo "ERROR: Cannot resolve path: $path" >&2
      exit 1
    }
  }

  # Warn if original path was a symlink (security audit)
  # Compare the user-provided path with the resolved real path
  local user_realpath
  user_realpath="$(realpath "$path" 2>/dev/null || echo "$path")"
  if [[ -L "$path" ]] || [[ "$user_realpath" != "$path" && "$user_realpath" != "$real_path" ]]; then
    echo "WARNING: Mount path is/contains symlink: $path -> $real_path" >&2
    echo "  Using resolved path for security" >&2
  fi

  # Validate name - alphanumeric, dash, underscore only
  if [[ ! "$name" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "ERROR: Invalid mount name '$name' - only alphanumeric, dash, underscore allowed" >&2
    exit 1
  fi

  # Check name length
  if [[ ${#name} -gt 64 ]]; then
    echo "ERROR: Mount name too long (max 64 chars): $name" >&2
    exit 1
  fi

  # Check reserved names (case-insensitive, Bash 3 compat)
  local name_lower
  name_lower=$(echo "$name" | tr '[:upper:]' '[:lower:]')
  for reserved in "${RESERVED_NAMES[@]}"; do
    local reserved_lower
    reserved_lower=$(echo "$reserved" | tr '[:upper:]' '[:lower:]')
    if [[ "$name_lower" == "$reserved_lower" ]]; then
      echo "ERROR: Reserved mount name cannot be used: $name" >&2
      exit 1
    fi
  done

  # Check for duplicate names (using string matching for Bash 3 compat)
  if [[ " ${USED_MOUNT_NAMES} " == *" ${name} "* ]]; then
    echo "ERROR: Duplicate mount name: $name" >&2
    exit 1
  fi
  USED_MOUNT_NAMES="${USED_MOUNT_NAMES} ${name}"

  # Warn about potentially sensitive paths
  local sensitive_patterns=(
    "/etc"
    "/var/log"
    "/root"
    "/.ssh"
    "/private/etc"
  )
  for pattern in "${sensitive_patterns[@]}"; do
    if [[ "$real_path" == *"$pattern"* ]]; then
      echo "WARNING: Mounting potentially sensitive path: $real_path" >&2
      break
    fi
  done

  echo "${real_path}:${name}"
}

# Load mounts from YAML configuration file
function load_mounts_from_yaml() {
  local config_file="config/external-mounts.yaml"

  if [[ ! -f "${config_file}" ]]; then
    # No YAML config file - that's OK, use CLI args only
    return 0
  fi

  echo "Loading mounts from ${config_file}..."

  # Parse YAML config using Python helper script
  local mounts_output
  mounts_output=$(python3 scripts/parse_mounts_config.py --config "${config_file}" 2>&1) || {
    echo "ERROR: Failed to parse ${config_file}:" >&2
    echo "${mounts_output}" >&2
    exit 1
  }

  # Process each mount line
  while IFS= read -r line; do
    if [[ -z "${line}" ]]; then
      continue
    fi

    # Format: MOUNT_RO:path:name or MOUNT_RW:path:name
    local mount_type="${line%%:*}"
    local rest="${line#*:}"
    local mount_path="${rest%%:*}"
    local mount_name="${rest##*:}"

    if [[ "${mount_type}" == "MOUNT_RO" ]]; then
      # Validate and add global RO mount
      local validated
      validated="$(validate_mount "$mount_path" "$mount_name" "ro")" || exit 1
      MOUNTS_RO+=("$validated")
      echo "  Added global RO mount: ${mount_name} -> ${mount_path}"
    elif [[ "${mount_type}" == "MOUNT_RW" ]]; then
      # Validate and add global RW mount
      local validated
      validated="$(validate_mount "$mount_path" "$mount_name" "rw")" || exit 1
      MOUNTS_RW+=("$validated")
      echo "  Added global RW mount: ${mount_name} -> ${mount_path}"
    elif [[ "${mount_type}" == "MOUNT_USER_RO" ]]; then
      # Validate and add per-user RO mount (mounted at /mounts/user-ro/{name})
      local validated
      validated="$(validate_mount "$mount_path" "$mount_name" "user-ro")" || exit 1
      MOUNTS_USER_RO+=("$validated")
      echo "  Added per-user RO mount: ${mount_name} -> ${mount_path}"
    elif [[ "${mount_type}" == "MOUNT_USER_RW" ]]; then
      # Validate and add per-user RW mount (mounted at /mounts/user-rw/{name})
      local validated
      validated="$(validate_mount "$mount_path" "$mount_name" "user-rw")" || exit 1
      MOUNTS_USER_RW+=("$validated")
      echo "  Added per-user RW mount: ${mount_name} -> ${mount_path}"
    elif [[ "${mount_type}" == "MOUNT_DYNAMIC" ]]; then
      # Dynamic mount base (format: MOUNT_DYNAMIC:path:name:mode)
      # The mode comes from the third field
      local mount_mode="${rest##*:}"
      # Re-extract name without mode
      rest="${line#*:}"
      rest="${rest#*:}"
      mount_name="${rest%%:*}"
      mount_mode="${rest##*:}"

      # Handle paths with {username} placeholder - skip validation for those
      if [[ "${mount_path}" == *"{username}"* ]]; then
        # Skip path existence validation for user-templated paths
        MOUNTS_DYNAMIC+=("${mount_path}:${mount_name}:${mount_mode}")
        echo "  Added dynamic base (templated): ${mount_name} -> ${mount_path} [${mount_mode}]"
      else
        local validated
        validated="$(validate_mount "$mount_path" "$mount_name" "dynamic")" || exit 1
        MOUNTS_DYNAMIC+=("${validated}:${mount_mode}")
        echo "  Added dynamic base: ${mount_name} -> ${mount_path} [${mount_mode}]"
      fi
    elif [[ "${mount_type}" == "MOUNT_ORIGINAL" ]]; then
      # Original-path mount (format: MOUNT_ORIGINAL:path:encoded:mode)
      # These mount host paths at /mounts/paths/{encoded} for access at original locations
      rest="${line#*:}"
      local orig_path="${rest%%:*}"
      rest="${rest#*:}"
      local encoded="${rest%%:*}"
      local mount_mode="${rest##*:}"

      # Validate the original path exists
      if [[ -e "${orig_path}" ]]; then
        MOUNTS_ORIGINAL+=("${orig_path}:${encoded}:${mount_mode}")
        echo "  Added original-path mount: ${orig_path} -> /mounts/paths/${encoded} [${mount_mode}]"
      else
        echo "  Skipping original-path mount (path not found): ${orig_path}"
      fi
    fi
  done <<< "${mounts_output}"
}

# Parse arguments
ACTION=""
NO_CACHE=""
TEST_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    setup|build|cleanup|restart|rebuild|test|lint|audit|shell|create-user|delete-user|cleanup-test-users)
      ACTION="$1"
      shift
      # For test command, collect remaining args
      if [[ "${ACTION}" == "test" ]]; then
        while [[ $# -gt 0 ]]; do
          TEST_ARGS+=("$1")
          shift
        done
      fi
      # For create-user command, collect remaining args
      if [[ "${ACTION}" == "create-user" ]]; then
        while [[ $# -gt 0 ]]; do
          TEST_ARGS+=("$1")
          shift
        done
      fi
      # For delete-user command, collect remaining args
      if [[ "${ACTION}" == "delete-user" ]]; then
        while [[ $# -gt 0 ]]; do
          TEST_ARGS+=("$1")
          shift
        done
      fi
      # For cleanup-test-users command, collect remaining args
      if [[ "${ACTION}" == "cleanup-test-users" ]]; then
        while [[ $# -gt 0 ]]; do
          TEST_ARGS+=("$1")
          shift
        done
      fi
      ;;
    --mount-rw=*)
      mount_spec="${1#--mount-rw=}"
      if [[ "$mount_spec" == *:* ]]; then
        mount_path="${mount_spec%%:*}"
        mount_name="${mount_spec##*:}"
      else
        mount_path="$mount_spec"
        mount_name="$(basename "$mount_path")"
      fi
      validated="$(validate_mount "$mount_path" "$mount_name" "rw")"
      MOUNTS_RW+=("$validated")
      shift
      ;;
    --mount-ro=*)
      mount_spec="${1#--mount-ro=}"
      if [[ "$mount_spec" == *:* ]]; then
        mount_path="${mount_spec%%:*}"
        mount_name="${mount_spec##*:}"
      else
        mount_path="$mount_spec"
        mount_name="$(basename "$mount_path")"
      fi
      validated="$(validate_mount "$mount_path" "$mount_name" "ro")"
      MOUNTS_RO+=("$validated")
      shift
      ;;
    --dev)
      AG3NTUM_MODE="dev"
      shift
      ;;
    --no-cache)
      NO_CACHE="--no-cache"
      shift
      ;;
    --help|-h)
      show_usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      show_usage
      exit 1
      ;;
  esac
done

if [[ -z "${ACTION}" ]]; then
  show_usage
  exit 1
fi

# =============================================================================
# Mode Detection (prod vs dev)
# =============================================================================
# Priority: CLI --dev flag > AG3NTUM_MODE env var > .env file > default (prod)
#
# Both modes serve Web UI on WEB_PORT (50080) and API on API_PORT (40080).
#
# prod (default): Web container serves pre-built static bundle (fast startup,
#                 no node_modules, no npm install).
# dev:            Web container runs Vite dev server with HMR and hot-reload.
if [[ -z "${AG3NTUM_MODE:-}" ]]; then
  if [[ -f .env ]] && grep -q '^AG3NTUM_MODE=' .env; then
    AG3NTUM_MODE="$(grep '^AG3NTUM_MODE=' .env | cut -d= -f2)"
  else
    AG3NTUM_MODE="prod"
  fi
fi

# Compose command varies by mode
if [[ "${AG3NTUM_MODE}" == "dev" ]]; then
  COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.dev.yml"
else
  COMPOSE_CMD="docker compose"
fi

# Compose command that always includes the web service with dev overlay (for UI tests).
# In prod mode, docker-compose.yml already includes the web service, but UI tests need
# the dev overlay for node_modules volume and Vite dev server.
COMPOSE_WITH_WEB="docker compose -f docker-compose.yml -f docker-compose.dev.yml"

function read_config_value() {
  local key="$1"
  local default="${2:-}"
  local config_file="config/api.yaml"

  # Return default if config file doesn't exist
  if [[ ! -f "$config_file" ]]; then
    echo "$default"
    return
  fi

  # Split key into section and field (e.g., "api.external_port" -> "api" "external_port")
  local section="${key%%.*}"
  local field="${key##*.}"

  # Parse simple nested YAML without external dependencies
  # Handles format:  section:
  #                    field: value
  local value
  value=$(awk -v section="$section" -v field="$field" '
    BEGIN { in_section = 0 }
    # Match section header (starts at column 0, ends with colon)
    /^[a-zA-Z_][a-zA-Z0-9_]*:/ {
      gsub(/:.*/, "", $0)
      in_section = ($0 == section) ? 1 : 0
      next
    }
    # Match field within section (indented, has colon)
    in_section && /^[[:space:]]+[a-zA-Z_][a-zA-Z0-9_]*:/ {
      # Extract field name (remove leading whitespace and trailing colon)
      fname = $0
      gsub(/^[[:space:]]+/, "", fname)
      gsub(/:.*/, "", fname)
      if (fname == field) {
        # Extract value (everything after first colon, trimmed)
        val = $0
        sub(/^[^:]*:[[:space:]]*/, "", val)
        gsub(/^["'\'']|["'\'']$/, "", val)  # Remove quotes
        print val
        exit
      }
    }
  ' "$config_file")

  # Return value if found, otherwise default
  if [[ -n "$value" ]]; then
    echo "$value"
  else
    echo "$default"
  fi
}

function render_ui_config() {
  # Read server configuration with defaults
  local HOSTNAME
  local PROTOCOL
  HOSTNAME="$(read_config_value 'server.hostname' 'localhost')"
  PROTOCOL="$(read_config_value 'server.protocol' 'http')"

  local target="src/web_terminal_client/public/config.yaml"

  # Remove existing file first (may be owned by container user from previous build)
  safe_remove_file "${target}"

  cat > "${target}" <<EOF
server:
  port: ${WEB_PORT}
  host: "0.0.0.0"

api:
  # API URL derived from server.hostname and server.protocol in api.yaml
  # Frontend will replace "localhost" with browser hostname if accessed remotely
  base_url: "${PROTOCOL}://${HOSTNAME}:${API_PORT}"

ui:
  max_output_lines: 1000
  auto_scroll: true
EOF

  echo "  Frontend config: ${PROTOCOL}://${HOSTNAME}:${API_PORT}"
}

function generate_compose_override() {
  # Generate docker-compose.override.yml with extra mounts if any were specified
  local override_file="docker-compose.override.yml"
  local manifest_file="auto-generated/auto-generated-mounts.yaml"

  # Ensure the auto-generated directory exists
  mkdir -p "auto-generated" 2>/dev/null || true

  # Remove existing generated files (may be owned by root from previous container build)
  safe_remove_file "${override_file}"
  safe_remove_file "${manifest_file}"

  if [[ ${#MOUNTS_RW[@]} -eq 0 && ${#MOUNTS_RO[@]} -eq 0 && ${#MOUNTS_USER_RW[@]} -eq 0 && ${#MOUNTS_USER_RO[@]} -eq 0 && ${#MOUNTS_DYNAMIC[@]} -eq 0 ]]; then
    # No mounts specified, create empty manifest (override already removed above)
    cat > "${manifest_file}" <<EOF
# =============================================================================
# AUTO-GENERATED FILE - DO NOT EDIT
# =============================================================================
# This file is automatically generated by run.sh from config/external-mounts.yaml
# Any manual changes will be overwritten on the next deployment.
#
# To configure mounts, edit: config/external-mounts.yaml
# Then run: ./run.sh build
#
# Purpose: This manifest maps Docker container paths to host filesystem paths,
# enabling symlink resolution when running outside Docker (development mode).
# =============================================================================
mounts:
  ro: []
  rw: []
EOF
    return
  fi

  # Generate docker-compose override for volume mounts
  cat > "${override_file}" <<EOF
# Auto-generated by run.sh - do not edit manually
# External mounts are available in agent sessions at:
#   Read-only:  /workspace/external/ro/{name}/
#   Read-write: /workspace/external/rw/{name}/
#   Persistent: /workspace/persistent/
services:
  ag3ntum-api:
    volumes:
EOF

  # Start manifest file - write header only
  cat > "${manifest_file}" <<EOF
# =============================================================================
# AUTO-GENERATED FILE - DO NOT EDIT
# =============================================================================
# This file is automatically generated by run.sh from config/external-mounts.yaml
# Any manual changes will be overwritten on the next deployment.
#
# To configure mounts, edit: config/external-mounts.yaml
# Then run: ./run.sh build
#
# Purpose: This manifest maps Docker container paths to host filesystem paths,
# enabling symlink resolution when running outside Docker (development mode).
# These mounts are available in agent sessions at /workspace/external/
# =============================================================================
mounts:
EOF

  # Write RO section (flattened: /mounts/{name} instead of /mounts/ro/{name})
  if [[ ${#MOUNTS_RO[@]} -gt 0 ]]; then
    echo "  ro:" >> "${manifest_file}"
    for mount in "${MOUNTS_RO[@]}"; do
      local abs_path="${mount%%:*}"
      local name="${mount##*:}"
      echo "      - ${abs_path}:/mounts/${name}:ro" >> "${override_file}"
      echo "    - name: \"${name}\"" >> "${manifest_file}"
      echo "      host_path: \"${abs_path}\"" >> "${manifest_file}"
      echo "      container_path: \"/mounts/${name}\"" >> "${manifest_file}"
      echo "      workspace_path: \"./external/ro/${name}\"" >> "${manifest_file}"
      echo "      mount_type: \"global_ro\"" >> "${manifest_file}"
      echo "      mode: \"ro\"" >> "${manifest_file}"
    done
  else
    echo "  ro: []" >> "${manifest_file}"
  fi

  # Write RW section (flattened: /mounts/{name} instead of /mounts/rw/{name})
  if [[ ${#MOUNTS_RW[@]} -gt 0 ]]; then
    echo "  rw:" >> "${manifest_file}"
    for mount in "${MOUNTS_RW[@]}"; do
      local abs_path="${mount%%:*}"
      local name="${mount##*:}"
      echo "      - ${abs_path}:/mounts/${name}:rw" >> "${override_file}"
      echo "    - name: \"${name}\"" >> "${manifest_file}"
      echo "      host_path: \"${abs_path}\"" >> "${manifest_file}"
      echo "      container_path: \"/mounts/${name}\"" >> "${manifest_file}"
      echo "      workspace_path: \"./external/rw/${name}\"" >> "${manifest_file}"
      echo "      mount_type: \"global_rw\"" >> "${manifest_file}"
      echo "      mode: \"rw\"" >> "${manifest_file}"
    done
  else
    echo "  rw: []" >> "${manifest_file}"
  fi

  # Write per-user RO section (flattened: /mounts/{name} instead of /mounts/user-ro/{name})
  if [[ ${#MOUNTS_USER_RO[@]} -gt 0 ]]; then
    echo "  user-ro:" >> "${manifest_file}"
    for mount in "${MOUNTS_USER_RO[@]}"; do
      local abs_path="${mount%%:*}"
      local name="${mount##*:}"
      echo "      - ${abs_path}:/mounts/${name}:ro" >> "${override_file}"
      echo "    - name: \"${name}\"" >> "${manifest_file}"
      echo "      host_path: \"${abs_path}\"" >> "${manifest_file}"
      echo "      container_path: \"/mounts/${name}\"" >> "${manifest_file}"
      echo "      workspace_path: \"./external/user-ro/${name}\"" >> "${manifest_file}"
      echo "      mount_type: \"user_ro\"" >> "${manifest_file}"
      echo "      mode: \"ro\"" >> "${manifest_file}"
    done
  else
    echo "  user-ro: []" >> "${manifest_file}"
  fi

  # Write per-user RW section (flattened: /mounts/{name} instead of /mounts/user-rw/{name})
  if [[ ${#MOUNTS_USER_RW[@]} -gt 0 ]]; then
    echo "  user-rw:" >> "${manifest_file}"
    for mount in "${MOUNTS_USER_RW[@]}"; do
      local abs_path="${mount%%:*}"
      local name="${mount##*:}"
      echo "      - ${abs_path}:/mounts/${name}:rw" >> "${override_file}"
      echo "    - name: \"${name}\"" >> "${manifest_file}"
      echo "      host_path: \"${abs_path}\"" >> "${manifest_file}"
      echo "      container_path: \"/mounts/${name}\"" >> "${manifest_file}"
      echo "      workspace_path: \"./external/user-rw/${name}\"" >> "${manifest_file}"
      echo "      mount_type: \"user_rw\"" >> "${manifest_file}"
      echo "      mode: \"rw\"" >> "${manifest_file}"
    done
  else
    echo "  user-rw: []" >> "${manifest_file}"
  fi

  # Write dynamic mount bases section (flattened: /mounts/{name} instead of /mounts/dynamic/{name})
  if [[ ${#MOUNTS_DYNAMIC[@]} -gt 0 ]]; then
    echo "  dynamic:" >> "${manifest_file}"
    for mount in "${MOUNTS_DYNAMIC[@]}"; do
      # Format: path:name:mode
      local abs_path="${mount%%:*}"
      local rest="${mount#*:}"
      local name="${rest%%:*}"
      local mode="${rest##*:}"

      # Skip paths with {username} placeholder for Docker volume (validated at session time)
      if [[ "${abs_path}" == *"{username}"* ]]; then
        echo "    - name: \"${name}\"" >> "${manifest_file}"
        echo "      host_path: \"${abs_path}\"" >> "${manifest_file}"
        echo "      container_path: \"/mounts/${name}\"" >> "${manifest_file}"
        echo "      max_mode: \"${mode}\"" >> "${manifest_file}"
        echo "      mount_type: \"dynamic\"" >> "${manifest_file}"
        echo "      has_placeholder: true" >> "${manifest_file}"
        echo "  Note: Dynamic base '${name}' has {username} placeholder - Docker volume skipped"
      else
        # Regular path - add Docker volume
        local docker_mode="ro"
        if [[ "${mode}" == "rw" ]]; then
          docker_mode="rw"
        fi
        echo "      - ${abs_path}:/mounts/${name}:${docker_mode}" >> "${override_file}"
        echo "    - name: \"${name}\"" >> "${manifest_file}"
        echo "      host_path: \"${abs_path}\"" >> "${manifest_file}"
        echo "      container_path: \"/mounts/${name}\"" >> "${manifest_file}"
        echo "      max_mode: \"${mode}\"" >> "${manifest_file}"
        echo "      mount_type: \"dynamic\"" >> "${manifest_file}"
        echo "      has_placeholder: false" >> "${manifest_file}"
      fi
    done
  else
    echo "  dynamic: []" >> "${manifest_file}"
  fi

  # Generate original-path mounts (if any)
  # These mount paths at /mounts/paths/{encoded} for access at original locations
  if [[ ${#MOUNTS_ORIGINAL[@]} -gt 0 ]]; then
    echo "  original_paths:" >> "${manifest_file}"
    for mount in "${MOUNTS_ORIGINAL[@]}"; do
      # Format: path:encoded:mode
      local orig_path="${mount%%:*}"
      local rest="${mount#*:}"
      local encoded="${rest%%:*}"
      local mode="${rest##*:}"

      # Get absolute path
      local abs_path
      abs_path="$(cd "$(dirname "${orig_path}")" 2>/dev/null && pwd)/$(basename "${orig_path}")"

      # Add Docker volume at /mounts/paths/{encoded}
      echo "      - ${abs_path}:/mounts/paths/${encoded}:${mode}" >> "${override_file}"

      # Add to manifest
      echo "    - path: \"${orig_path}\"" >> "${manifest_file}"
      echo "      encoded: \"${encoded}\"" >> "${manifest_file}"
      echo "      host_path: \"${abs_path}\"" >> "${manifest_file}"
      echo "      container_path: \"/mounts/paths/${encoded}\"" >> "${manifest_file}"
      echo "      mode: \"${mode}\"" >> "${manifest_file}"
      echo "      mount_type: \"original_path\"" >> "${manifest_file}"
    done
  else
    echo "  original_paths: []" >> "${manifest_file}"
  fi

  echo ""
  echo "=== External Mounts Configured ==="
  echo "Generated ${override_file}"
  echo "Generated ${manifest_file}"
  echo ""
  if [[ ${#MOUNTS_RO[@]} -gt 0 ]]; then
    echo "Read-only mounts (agent cannot modify):"
    for mount in "${MOUNTS_RO[@]}"; do
      local name="${mount##*:}"
      echo "  ./external/ro/${name}/"
    done
  fi
  if [[ ${#MOUNTS_RW[@]} -gt 0 ]]; then
    echo "Read-write mounts (agent can modify):"
    for mount in "${MOUNTS_RW[@]}"; do
      local name="${mount##*:}"
      echo "  ./external/rw/${name}/"
    done
  fi
  if [[ ${#MOUNTS_DYNAMIC[@]} -gt 0 ]]; then
    echo "Dynamic mount bases (user-selectable per session):"
    for mount in "${MOUNTS_DYNAMIC[@]}"; do
      local rest="${mount#*:}"
      local name="${rest%%:*}"
      local mode="${rest##*:}"
      echo "  ./${name}/ [max: ${mode}]"
    done
  fi
  if [[ ${#MOUNTS_ORIGINAL[@]} -gt 0 ]]; then
    echo "Original-path mounts (accessible at original locations):"
    for mount in "${MOUNTS_ORIGINAL[@]}"; do
      local orig_path="${mount%%:*}"
      local rest="${mount#*:}"
      local mode="${rest##*:}"
      echo "  ${orig_path} [${mode}]"
    done
  fi
  echo "Persistent storage (always available):"
  echo "  ./persistent/"
  echo ""
}

function check_services() {
  local missing=0
  local running
  running="$(${COMPOSE_CMD} ps --status running --services || true)"
  if ! grep -q "ag3ntum-api" <<<"${running}"; then
    echo "Service not running: ag3ntum-api"
    missing=1
  fi
  if ! grep -q "ag3ntum-web" <<<"${running}"; then
    echo "Service not running: ag3ntum-web"
    missing=1
  fi
  return "${missing}"
}

function do_cleanup() {
  # Determine project name for scoped cleanup
  local project_name=""
  if [[ -f .env ]] && grep -q '^COMPOSE_PROJECT_NAME=' .env; then
    project_name="$(grep '^COMPOSE_PROJECT_NAME=' .env | cut -d= -f2)"
  else
    project_name="$(basename "$(pwd)" | tr '[:upper:]' '[:lower:]')"
  fi

  echo "=== Starting cleanup for instance: ${project_name} ==="

  # Step 1: Stop and remove project containers (scoped by COMPOSE_PROJECT_NAME in .env)
  # Use --remove-orphans to also clean up dev-mode web containers when in prod mode
  echo "Stopping containers..."
  ${COMPOSE_CMD} down --remove-orphans --timeout 10 2>/dev/null || true

  # Step 2: Remove project-specific volumes
  echo "Removing project volumes..."
  docker volume ls --filter "name=${project_name}_" -q 2>/dev/null | xargs -r docker volume rm 2>/dev/null || true

  # Step 3: Remove project-specific networks
  echo "Removing project networks..."
  docker network ls --filter "name=${project_name}_" -q 2>/dev/null | xargs -r docker network rm 2>/dev/null || true

  # Step 3.5: Reclaim ownership of container-owned directories (no sudo needed)
  local os_type
  os_type="$(uname -s)"
  if [[ "${os_type}" == "Linux" ]] && [[ "$(id -u)" != "0" ]]; then
    echo "Reclaiming directory ownership..."
    local me
    me="$(id -u):$(id -g)"
    # Use any available ag3ntum image (still exists at this point, before image removal)
    local reclaim_image
    reclaim_image=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep "^${IMAGE_PREFIX}:" | head -1 || true)
    if [[ -z "${reclaim_image}" ]]; then
      reclaim_image="alpine"
    fi
    local mount_args=""
    for dir in logs data users; do
      if [[ -d "$dir" ]]; then
        mount_args="${mount_args} -v $(pwd)/${dir}:/${dir}"
      fi
    done
    if [[ -n "${mount_args}" ]]; then
      docker run --rm ${mount_args} "${reclaim_image}" chown -R "${me}" /logs /data /users 2>/dev/null || true
    fi
    # Also reclaim config/secrets.yaml
    if [[ -f "config/secrets.yaml" ]] && [[ ! -w "config/secrets.yaml" ]]; then
      docker run --rm -v "$(pwd)/config:/config" "${reclaim_image}" chown "${me}" /config/secrets.yaml 2>/dev/null || true
    fi
  fi

  # Step 4: Remove ag3ntum images only if no other ag3ntum instances are running
  # After docker compose down above, our containers are stopped. Check if any
  # still-running containers use ag3ntum images (= other worktree instances).
  # This avoids false positives from unrelated compose projects (postgres, etc.).
  local other_ag3ntum
  other_ag3ntum=$(docker ps --format '{{.Image}}' 2>/dev/null | grep "^${IMAGE_PREFIX}:" || true)
  if [[ -z "${other_ag3ntum}" ]]; then
    echo "No other instances running. Removing ${IMAGE_PREFIX} images..."
    local images
    images=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep "^${IMAGE_PREFIX}:" || true)
    if [[ -n "${images}" ]]; then
      echo "  Removing: ${images}"
      echo "${images}" | xargs -r docker rmi -f 2>/dev/null || true
    fi

    # Remove third-party images pulled by this compose file (e.g. redis)
    # only if no other containers still reference them
    local compose_images
    compose_images=$(${COMPOSE_CMD} config --images 2>/dev/null || true)
    for img in ${compose_images}; do
      # Skip ag3ntum images — already handled above
      [[ "${img}" == "${IMAGE_PREFIX}:"* ]] && continue
      if docker images --format '{{.Repository}}:{{.Tag}}' | grep -qF "${img}"; then
        local users
        users=$(docker ps -a --filter "ancestor=${img}" -q 2>/dev/null || true)
        if [[ -z "${users}" ]]; then
          echo "  Removing ${img}..."
          docker rmi "${img}" 2>/dev/null || true
        else
          echo "  Preserving ${img} — still used by other containers."
        fi
      fi
    done

    # Also remove any dangling images
    local dangling
    dangling=$(docker images -q --filter "dangling=true" 2>/dev/null || true)
    if [[ -n "${dangling}" ]]; then
      echo "  Removing dangling images..."
      echo "${dangling}" | xargs -r docker rmi -f 2>/dev/null || true
    fi
  else
    echo "Other ag3ntum instances still running — preserving shared images."
  fi

  # Step 5: Remove generated files
  echo "Removing generated files..."
  rm -f docker-compose.override.yml
  rm -f .env.bak
  rm -f src/web_terminal_client/public/config.yaml 2>/dev/null || true

  # Step 6: Check for orphaned processes on configured ports
  echo "Checking for orphaned processes on configured ports..."
  local api_port="${1:-40080}"
  local web_port="${2:-50080}"

  # Check if ports are in use by non-docker processes
  for port in "${api_port}" "${web_port}"; do
    local pid
    pid=$(lsof -ti ":${port}" 2>/dev/null || true)
    if [[ -n "${pid}" ]]; then
      # Check if it's a docker process - if not, warn (don't kill)
      local proc_name
      proc_name=$(ps -p "${pid}" -o comm= 2>/dev/null || true)
      if [[ "${proc_name}" != *"docker"* && "${proc_name}" != *"com.docker"* ]]; then
        echo "  WARNING: Port ${port} is in use by non-Docker process: ${proc_name} (PID ${pid})"
        echo "           You may need to kill it manually: kill ${pid}"
      fi
    fi
  done

  echo "=== Cleanup complete ==="
}

function do_restart() {
  echo "=== Restarting containers to reload code (mode: ${AG3NTUM_MODE}) ==="

  # Restart both containers
  echo "Restarting ag3ntum-api..."
  ${COMPOSE_CMD} restart ag3ntum-api

  echo "Restarting ag3ntum-web..."
  ${COMPOSE_CMD} restart ag3ntum-web

  # Wait for services to be healthy
  sleep 2

  if check_services; then
    echo "=== Restart complete - services running ==="
  else
    echo "=== WARNING: Some services may not be running ==="
    ${COMPOSE_CMD} ps
  fi
}

function create_user() {
  USERNAME=""
  EMAIL=""
  PASSWORD=""
  ADMIN=""

  # Parse arguments
  for arg in "$@"; do
    case "$arg" in
      --username=*) USERNAME="${arg#--username=}" ;;
      --email=*) EMAIL="${arg#--email=}" ;;
      --password=*) PASSWORD="${arg#--password=}" ;;
      --admin) ADMIN="--admin" ;;
    esac
  done

  # Validate required arguments
  if [[ -z "$USERNAME" || -z "$EMAIL" || -z "$PASSWORD" ]]; then
    echo "Error: Missing required arguments"
    echo "Usage: ./run.sh create-user --username=USER --email=EMAIL --password=PASS [--admin]"
    echo ""
    echo "UID Security Mode (set via environment or docker-compose.yml):"
    echo "  AG3NTUM_UID_MODE=isolated  (default) UIDs 50000-60000, multi-tenant safe"
    echo "  AG3NTUM_UID_MODE=direct    UIDs map to host (1000-65533), dev/single-tenant"
    exit 1
  fi

  # Check if container is running
  if ! ${COMPOSE_CMD} ps --status running --services 2>/dev/null | grep -q "ag3ntum-api"; then
    echo "Error: ag3ntum-api container is not running."
    echo "Start it first with: ./run.sh build"
    exit 1
  fi

  # Get current UID mode from container
  local uid_mode
  uid_mode=$(${COMPOSE_CMD} exec -T ag3ntum-api printenv AG3NTUM_UID_MODE 2>/dev/null | tr -d '\r' || echo "isolated")

  echo "=== Creating user: $USERNAME ==="
  echo "  UID Security Mode: ${uid_mode:-isolated}"

  # Run create_user.py inside container as root (avoids sudo prompts)
  ${COMPOSE_CMD} exec -T -u root ag3ntum-api \
    python3 src/cli/create_user.py \
    --username="$USERNAME" \
    --email="$EMAIL" \
    --password="$PASSWORD" \
    $ADMIN

  # Restart API so the process inherits the new user's group (Gotcha #12).
  # Without this, session directory access fails with PermissionError because
  # setpriv --init-groups only reads /etc/group at process start.
  echo ""
  echo "Restarting API to activate user access..."
  ${COMPOSE_CMD} restart ag3ntum-api
}

# Function to delete a user
function delete_user() {
  USERNAME=""
  FORCE=""

  # Parse arguments
  for arg in "$@"; do
    case "$arg" in
      --username=*) USERNAME="${arg#--username=}" ;;
      --force) FORCE="--force" ;;
    esac
  done

  # Validate required arguments
  if [[ -z "$USERNAME" ]]; then
    echo "Error: Missing required argument --username"
    echo "Usage: ./run.sh delete-user --username=USER [--force]"
    echo ""
    echo "Options:"
    echo "  --username=USER   Username to delete (required)"
    echo "  --force           Confirm deletion (required to actually delete)"
    echo ""
    echo "Note: This removes the user from Ag3ntum database and cleans up their"
    echo "      user directory. The Linux user account is preserved."
    exit 1
  fi

  # Check if container is running
  if ! ${COMPOSE_CMD} ps --status running --services 2>/dev/null | grep -q "ag3ntum-api"; then
    echo "Error: ag3ntum-api container is not running."
    echo "Start it first with: ./run.sh build"
    exit 1
  fi

  if [[ -z "$FORCE" ]]; then
    echo "=== User deletion preview ==="
  else
    echo "=== Deleting user: $USERNAME ==="
  fi

  # Run delete_user.py inside container as root (needs elevated permissions)
  ${COMPOSE_CMD} exec -T -u root ag3ntum-api \
    python3 src/cli/delete_user.py \
    --username="$USERNAME" \
    $FORCE
}

# Handle cleanup action
if [[ "${ACTION}" == "cleanup" ]]; then
  do_cleanup
  exit 0
fi

# Handle restart action
if [[ "${ACTION}" == "restart" ]]; then
  do_restart
  exit 0
fi

# Function to run UI/React tests
run_ui_tests() {
  echo "=== Running UI/React tests ==="

  # UI tests always need the web container in dev mode (with node_modules).
  # COMPOSE_WITH_WEB includes docker-compose.dev.yml for Vite + node_modules.
  # Always call up -d: if web is running in prod mode (from ./run.sh build),
  # compose detects the config change and recreates it with the dev overlay.
  echo "Ensuring ag3ntum-web container is running (dev mode)..."
  ${COMPOSE_WITH_WEB} up -d ag3ntum-web

  # Wait for entrypoint to complete (npm install + vite startup).
  # The entrypoint installs packages as ag3ntum_api (UID 45045). We must NOT
  # exec npm commands as root while it's running — that creates root-owned
  # /tmp/.npm cache files that cause EACCES on the next entrypoint npm install.
  echo "Waiting for Vite dev server to be ready..."
  local attempts=0
  local max_attempts=60
  while [[ $attempts -lt $max_attempts ]]; do
    if ! ${COMPOSE_WITH_WEB} ps --status running --services 2>/dev/null | grep -q "ag3ntum-web"; then
      echo "Error: ag3ntum-web container failed to start."
      echo "Check logs with: ${COMPOSE_WITH_WEB} logs ag3ntum-web"
      return 1
    fi
    # Check if vite is ready by looking for "VITE.*ready" in container logs
    if ${COMPOSE_WITH_WEB} logs ag3ntum-web 2>&1 | grep -q "VITE.*ready"; then
      echo "  Vite dev server ready."
      break
    fi
    attempts=$((attempts + 1))
    sleep 2
  done
  if [[ $attempts -ge $max_attempts ]]; then
    echo "Error: Vite dev server did not start within ${max_attempts}s."
    echo "Check logs with: ${COMPOSE_WITH_WEB} logs ag3ntum-web"
    return 1
  fi

  # Check if node_modules needs reinstalling (platform mismatch between host and container).
  # The bind-mounted node_modules may have wrong platform binaries (darwin vs linux).
  # Run as ag3ntum_api (45045) to match entrypoint's ownership of /tmp/.npm cache.
  echo "Checking node_modules platform compatibility..."
  NEEDS_REINSTALL=$(${COMPOSE_WITH_WEB} exec -T -u 45045:45045 ag3ntum-web sh -c '
    if [ ! -d /app/node_modules ]; then
      echo "missing"
    elif [ ! -d /app/node_modules/@rollup ]; then
      echo "missing_rollup"
    elif ! ls /app/node_modules/@rollup/rollup-linux-* >/dev/null 2>&1; then
      echo "wrong_platform"
    else
      echo "ok"
    fi
  ' 2>/dev/null | tr -d '\r')

  if [[ "${NEEDS_REINSTALL}" != "ok" ]]; then
    echo "Reinstalling node_modules for Linux platform (reason: ${NEEDS_REINSTALL})..."
    ${COMPOSE_WITH_WEB} exec -T -u 45045:45045 ag3ntum-web sh -c '
      cp /src/web_terminal_client/package.json /app/package.json && \
      cd /app && \
      rm -rf node_modules/* node_modules/.[!.]* 2>/dev/null; \
      npm install --no-fund --no-audit --no-package-lock
    '
  fi

  # Run vite build first to catch Babel transpilation errors
  # (Vitest uses esbuild which is more permissive than Babel)
  echo "Running vite build to verify transpilation..."
  if ! ${COMPOSE_WITH_WEB} exec -T -u 45045:45045 ag3ntum-web sh -c 'cd /src/web_terminal_client && vite build --config /tmp/vite-${AG3NTUM_WEB_PORT:-50080}/vite.config.mjs'; then
    echo ""
    echo "ERROR: Vite build failed. Fix transpilation errors before running tests."
    return 1
  fi
  echo "Build successful."
  echo ""

  # Run vitest inside the Docker container
  echo "Running vitest in Docker container..."
  ${COMPOSE_WITH_WEB} exec -T -u 45045:45045 -e FORCE_COLOR=1 ag3ntum-web sh -c 'cd /src/web_terminal_client && vitest run --config /tmp/vite-${AG3NTUM_WEB_PORT:-50080}/vitest.config.mjs'
  return $?
}

# Handle test action
if [[ "${ACTION}" == "test" ]]; then
  echo "=== Running tests ==="

  # Prevent concurrent test runs — two ./run.sh test invocations sharing the
  # same container race on container lifecycle (test mode → production restore).
  # When one finishes and restores the container, it kills the other mid-flight.
  TEST_LOCK_FILE="${ROOT_DIR}/.test.lock"

  # Try to acquire lock (atomic via mkdir — works across Linux and macOS)
  cleanup_test_lock() {
    rm -f "${TEST_LOCK_FILE}" 2>/dev/null || true
  }

  if [[ -f "${TEST_LOCK_FILE}" ]]; then
    LOCK_PID=$(cat "${TEST_LOCK_FILE}" 2>/dev/null || echo "")
    if [[ -n "${LOCK_PID}" ]] && kill -0 "${LOCK_PID}" 2>/dev/null; then
      echo "Error: Another test run is already in progress (PID ${LOCK_PID})."
      echo "If this is stale, remove ${TEST_LOCK_FILE} and retry."
      exit 1
    else
      # Stale lock from a crashed run — clean it up
      if [[ -n "${LOCK_PID}" ]]; then
        echo "Removing stale test lock (PID ${LOCK_PID} is not running)."
      fi
      cleanup_test_lock
    fi
  fi

  # Write our PID to the lock file
  echo $$ > "${TEST_LOCK_FILE}"
  trap cleanup_test_lock EXIT

  # Set up test logging - output goes to both console and log file
  TEST_LOG_FILE="logs/latest-test-results.log"
  mkdir -p logs 2>/dev/null || true

  # Remove stale log file that may be owned by container user from previous build
  safe_remove_file "$TEST_LOG_FILE"

  # Check if we can write to the log file (directory may be owned by container UID)
  if touch "$TEST_LOG_FILE" 2>/dev/null; then
    # Initialize log file with header
    {
      echo "========================================"
      echo "Test Run: $(date '+%Y-%m-%d %H:%M:%S')"
      echo "========================================"
      echo ""
    } > "$TEST_LOG_FILE"
    CAN_LOG=1
  elif ${COMPOSE_CMD} ps --status running --services 2>/dev/null | grep -q "ag3ntum-api"; then
    # logs/ directory is owned by container UID (45045). Use the running container
    # to create a world-writable log file so tee -a can append without sudo.
    ${COMPOSE_CMD} exec -T ag3ntum-api sh -c "
      echo '========================================' > /logs/latest-test-results.log &&
      echo 'Test Run: $(date '+%Y-%m-%d %H:%M:%S')' >> /logs/latest-test-results.log &&
      echo '========================================' >> /logs/latest-test-results.log &&
      echo '' >> /logs/latest-test-results.log &&
      chmod 646 /logs/latest-test-results.log
    "
    CAN_LOG=1
  else
    echo "Warning: Cannot write to ${TEST_LOG_FILE} (permission denied)"
    echo "Test output will only be shown in console."
    TEST_LOG_FILE="/dev/null"
    CAN_LOG=0
  fi

  # Helper function to run commands with tee (preserves exit code)
  run_with_log() {
    # Run command, tee to log file, preserve exit code
    "$@" 2>&1 | tee -a "$TEST_LOG_FILE"
    return "${PIPESTATUS[0]}"
  }

  # Use test compose override for test runs
  # This mounts test sudoers and uses test entrypoint
  COMPOSE_TEST="docker compose -f docker-compose.yml -f docker-compose.test.yml"

  # Exec options for running commands as ag3ntum_api (required because container starts as root)
  # The test container starts as root to install sudoers, then drops to ag3ntum_api for uvicorn.
  # But docker exec defaults to root, so we need to specify the user explicitly.
  EXEC_OPTS="-T -u ag3ntum_api"

  # Check if test configuration files exist
  if [[ ! -f "docker-compose.test.yml" ]]; then
    echo "Error: docker-compose.test.yml not found"
    echo "This file is required for running tests with proper permissions."
    exit 1
  fi

  if [[ ! -f "config/test/sudoers-test" ]]; then
    echo "Error: config/test/sudoers-test not found"
    echo "This file is required for integration tests that need elevated permissions."
    exit 1
  fi

  if [[ ! -f "entrypoint-test.sh" ]]; then
    echo "Error: entrypoint-test.sh not found"
    echo "This script is required to inject test sudoers at runtime."
    exit 1
  fi

  # Ensure container is running with test configuration
  # This restarts the API container with test volumes and entrypoint
  echo "Configuring container for test mode..."
  ${COMPOSE_TEST} up -d ag3ntum-api

  # Wait for container to be ready
  echo "Waiting for container to be ready..."
  sleep 2

  # Verify container is running
  if ! ${COMPOSE_TEST} ps --status running --services 2>/dev/null | grep -q "ag3ntum-api"; then
    echo "Error: Failed to start ag3ntum-api container in test mode."
    echo "Check logs with: ${COMPOSE_TEST} logs ag3ntum-api"
    exit 1
  fi

  echo ""

  # Build pytest command (--color=yes forces colors even when piped through tee)
  PYTEST_CMD="python -m pytest --color=yes"

  # Parse test arguments
  # Default: run ALL tests (backend+e2e+security+sandboxing+UI)
  # Specific flags run only that subset
  QUICK_MODE=""
  SUBSET=""
  BACKEND_ONLY=""
  UI_ONLY=""
  SECURITY_ONLY=""
  E2E_ONLY=""
  SANDBOXING_ONLY=""
  ALL_MODE=""

  ARGS_ARRAY=(${TEST_ARGS[@]+"${TEST_ARGS[@]}"})
  i=0
  while [[ $i -lt ${#ARGS_ARRAY[@]} ]]; do
    arg="${ARGS_ARRAY[$i]}"
    case "${arg}" in
      --quick)
        QUICK_MODE="1"
        ;;
      --backend)
        BACKEND_ONLY="1"
        ;;
      --ui|--frontend)
        UI_ONLY="1"
        ;;
      --security)
        SECURITY_ONLY="1"
        ;;
      --all)
        ALL_MODE="1"
        ;;
      --only-e2e)
        E2E_ONLY="1"
        ;;
      --e2e)
        E2E_ONLY="1"
        ;;
      --sandboxing)
        SANDBOXING_ONLY="1"
        ;;
      --subset)
        i=$((i + 1))
        if [[ $i -lt ${#ARGS_ARRAY[@]} ]]; then
          SUBSET="${ARGS_ARRAY[$i]}"
        else
          echo "Error: --subset requires a comma-separated list of test names"
          exit 1
        fi
        ;;
      --subset=*)
        SUBSET="${arg#--subset=}"
        ;;
      *)
        echo "Unknown test option: ${arg}"
        echo ""
        echo "Usage: ./run.sh test [OPTIONS]"
        echo ""
        echo "Options (default: run tests excluding E2E):"
        echo "  --all         Run ALL tests including end-to-end"
        echo "  --backend     Run only backend tests (no e2e)"
        echo "  --security    Run only security tests"
        echo "  --only-e2e    Run only e2e tests"
        echo "  --e2e         Alias for --only-e2e"
        echo "  --sandboxing  Run only sandboxing tests"
        echo "  --ui          Run only UI/frontend tests"
        echo "  --quick       Run fast tests only (no e2e/slow)"
        echo "  --subset X    Run tests matching pattern X"
        exit 1
        ;;
    esac
    i=$((i + 1))
  done

  # Handle UI-only mode
  if [[ -n "${UI_ONLY}" ]]; then
    run_ui_tests
    exit $?
  fi

  # For backend tests, verify container is still running
  if ! ${COMPOSE_TEST} ps --status running --services 2>/dev/null | grep -q "ag3ntum-api"; then
    echo "Error: ag3ntum-api container is not running."
    echo "Start it first with: ./run.sh build"
    exit 1
  fi

  # Handle specific test suite modes
  if [[ -n "${SECURITY_ONLY}" ]]; then
    echo "=== Running security tests only ===" | tee -a "$TEST_LOG_FILE"
    run_with_log ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api ${PYTEST_CMD} tests/security/ -v --tb=short
    TEST_RESULT=$?
    echo "" | tee -a "$TEST_LOG_FILE"
    echo "Restoring container to normal mode..."
    ${COMPOSE_CMD} up -d ag3ntum-api ag3ntum-web
    exit ${TEST_RESULT}
  fi

  if [[ -n "${E2E_ONLY}" ]]; then
    echo "=== Running e2e tests only ===" | tee -a "$TEST_LOG_FILE"
    run_with_log ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api ${PYTEST_CMD} tests/backend/ --run-e2e -v --tb=short -m "e2e"
    TEST_RESULT=$?
    echo "" | tee -a "$TEST_LOG_FILE"
    echo "Restoring container to normal mode..."
    ${COMPOSE_CMD} up -d ag3ntum-api ag3ntum-web
    exit ${TEST_RESULT}
  fi

  if [[ -n "${SANDBOXING_ONLY}" ]]; then
    echo "=== Running sandboxing tests only ===" | tee -a "$TEST_LOG_FILE"
    # Look for sandboxing tests in various locations
    SANDBOX_DIRS=""
    if ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api test -d /tests/sandboxing 2>/dev/null; then
      SANDBOX_DIRS="/tests/sandboxing/"
    fi
    # Also run sandbox-related tests in backend
    run_with_log ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api ${PYTEST_CMD} ${SANDBOX_DIRS} tests/backend/test_sandbox*.py -v --tb=short
    TEST_RESULT=$?
    echo "" | tee -a "$TEST_LOG_FILE"
    echo "Restoring container to normal mode..."
    ${COMPOSE_CMD} up -d ag3ntum-api ag3ntum-web
    exit ${TEST_RESULT}
  fi

  if [[ -n "${BACKEND_ONLY}" ]]; then
    echo "=== Running backend tests only (E2E skipped — use --all for E2E) ===" | tee -a "$TEST_LOG_FILE"
    run_with_log ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api ${PYTEST_CMD} tests/backend/ -v --tb=short
    TEST_RESULT=$?
    echo "" | tee -a "$TEST_LOG_FILE"
    echo "Restoring container to normal mode..."
    ${COMPOSE_CMD} up -d ag3ntum-api ag3ntum-web
    exit ${TEST_RESULT}
  fi

  # Build test arguments
  PYTEST_ARGS=()

  if [[ -n "${SUBSET}" ]]; then
    # Run specific tests by name pattern
    # Convert comma-separated names to test file paths
    TEST_FILES=()
    IFS=',' read -ra NAMES <<< "${SUBSET}"
    for name in "${NAMES[@]}"; do
      # Trim whitespace
      name="${name// /}"
      # Strip "test_" prefix if user included it (e.g., "test_llm_proxy" -> "llm_proxy")
      name="${name#test_}"
      # Also strip ".py" suffix if present
      name="${name%.py}"
      # Find matching test files by filename or directory name
      MATCHES=$(${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api find /tests -name "test_*${name}*.py" 2>/dev/null | sort -u)
      # Also match directories containing the pattern (e.g., --subset "core-tests" matches /tests/core-tests/)
      if [[ -z "${MATCHES}" ]]; then
        MATCHES=$(${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api find /tests -type d -name "*${name}*" -exec find {} -name "test_*.py" \; 2>/dev/null | sort -u)
      fi
      if [[ -n "${MATCHES}" ]]; then
        while IFS= read -r file; do
          TEST_FILES+=("${file}")
        done <<< "${MATCHES}"
      fi
    done

    if [[ ${#TEST_FILES[@]} -eq 0 ]]; then
      echo "No test files found matching: ${SUBSET}"
      echo ""
      echo "Available test files:"
      ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api find /tests -name "test_*.py" | sort
      exit 1
    fi

    # Add unique test files to args
    for file in $(printf '%s\n' "${TEST_FILES[@]}" | sort -u); do
      PYTEST_ARGS+=("${file}")
    done

    # Include --run-e2e if any subset test might have E2E tests
    PYTEST_ARGS+=("--run-e2e")
  else
    # Run all tests - need separate runs for backend (with --run-e2e) and others
    if [[ -n "${QUICK_MODE}" ]]; then
      # Quick mode: exclude E2E and slow tests (all tests at once, no --run-e2e)
      echo "Running quick tests (excluding E2E and slow tests)..."
      PYTEST_ARGS+=("tests/" "-v" "--tb=short")

      echo "Running: ${PYTEST_CMD} ${PYTEST_ARGS[*]}"
      echo ""

      # Run backend tests in container with logging
      # Use || true to prevent set -e from exiting on test failures
      BACKEND_RESULT=0
      if [[ -z "${UI_ONLY}" ]]; then
        run_with_log ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api ${PYTEST_CMD} "${PYTEST_ARGS[@]}" || BACKEND_RESULT=$?
      fi

      # Run UI tests unless backend-only
      UI_RESULT=0
      if [[ -z "${BACKEND_ONLY}" ]]; then
        echo "" | tee -a "$TEST_LOG_FILE"
        run_ui_tests 2>&1 | tee -a "$TEST_LOG_FILE"
        UI_RESULT=${PIPESTATUS[0]}
      fi

      # Print summary for quick mode (to console and log)
      {
        echo ""
        echo "========================================"
        echo "=== QUICK TEST SUMMARY ==="
        echo "========================================"
        if [[ -z "${UI_ONLY}" ]]; then
          if [[ ${BACKEND_RESULT} -eq 0 ]]; then
            echo "  ✓ Backend tests:  PASSED"
          else
            echo "  ✗ Backend tests:  FAILED"
          fi
        fi
        if [[ -z "${BACKEND_ONLY}" ]]; then
          if [[ ${UI_RESULT} -eq 0 ]]; then
            echo "  ✓ UI tests:       PASSED"
          else
            echo "  ✗ UI tests:       FAILED"
          fi
        fi
        echo "========================================"
        echo ""
        echo "Test results saved to: ${TEST_LOG_FILE}"
      } | tee -a "$TEST_LOG_FILE"

      # Restore container to production mode
      echo ""
      echo "Restoring container to normal mode..."
      ${COMPOSE_CMD} up -d ag3ntum-api ag3ntum-web

      if [[ ${BACKEND_RESULT} -ne 0 || ${UI_RESULT} -ne 0 ]]; then
        echo "" | tee -a "$TEST_LOG_FILE"
        echo "Some tests failed!" | tee -a "$TEST_LOG_FILE"
        exit 1
      fi
      exit 0
    else
      # Full mode: run all tests. E2E only included with --all flag.
      if [[ -n "${ALL_MODE}" ]]; then
        echo "Running ALL tests (backend with E2E + security + other tests)..." | tee -a "$TEST_LOG_FILE"
      else
        echo "Running tests (E2E skipped — use --all or --only-e2e for E2E)..." | tee -a "$TEST_LOG_FILE"
      fi
      echo "" | tee -a "$TEST_LOG_FILE"

      # First run: backend tests (with --run-e2e only if --all)
      if [[ -n "${ALL_MODE}" ]]; then
        echo "=== Running backend tests (with E2E) ===" | tee -a "$TEST_LOG_FILE"
      else
        echo "=== Running backend tests ===" | tee -a "$TEST_LOG_FILE"
      fi
      BACKEND_RESULT=0
      if [[ -n "${ALL_MODE}" ]]; then
        run_with_log ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api ${PYTEST_CMD} tests/backend/ --run-e2e -v --tb=short || BACKEND_RESULT=$?
      else
        run_with_log ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api ${PYTEST_CMD} tests/backend/ -v --tb=short || BACKEND_RESULT=$?
      fi

      # Second run: security tests (no --run-e2e flag)
      echo "" | tee -a "$TEST_LOG_FILE"
      echo "=== Running security tests ===" | tee -a "$TEST_LOG_FILE"
      SECURITY_RESULT=0
      run_with_log ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api ${PYTEST_CMD} tests/security/ -v --tb=short || SECURITY_RESULT=$?

      # Check for other test directories and run them
      OTHER_DIRS=$(${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api find /tests -maxdepth 1 -type d ! -name backend ! -name security ! -name __pycache__ ! -name tests 2>/dev/null | grep -v "^/tests$" || true)
      OTHER_RESULT=0

      if [[ -n "${OTHER_DIRS}" ]]; then
        for dir in ${OTHER_DIRS}; do
          dir_name=$(basename "${dir}")
          if [[ "${dir_name}" != ".DS_Store" && "${dir_name}" != "__pycache__" ]]; then
            # Check if directory has any test files
            HAS_TESTS=$(${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api find "${dir}" -name "test_*.py" 2>/dev/null | head -1)
            if [[ -n "${HAS_TESTS}" ]]; then
              echo "" | tee -a "$TEST_LOG_FILE"
              echo "=== Running ${dir_name} tests ===" | tee -a "$TEST_LOG_FILE"
              DIR_RESULT=0
              run_with_log ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api ${PYTEST_CMD} "${dir}/" -v --tb=short || DIR_RESULT=$?
              if [[ ${DIR_RESULT} -ne 0 ]]; then
                OTHER_RESULT=1
              fi
            fi
          fi
        done
      fi

      # Run UI tests if not backend-only mode
      UI_RESULT=0
      if [[ -z "${BACKEND_ONLY}" ]]; then
        echo "" | tee -a "$TEST_LOG_FILE"
        run_ui_tests 2>&1 | tee -a "$TEST_LOG_FILE"
        UI_RESULT=${PIPESTATUS[0]}
      fi

      # Print combined summary (to console and log)
      TOTAL_BACKEND=$(${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api python -m pytest tests/ --collect-only -q 2>/dev/null | tail -1 | grep -oE '[0-9]+' | head -1)
      {
        echo ""
        echo "========================================"
        echo "=== COMBINED TEST SUMMARY ==="
        echo "========================================"
        echo "Backend tests in suite: ${TOTAL_BACKEND:-302}"
        echo ""
        if [[ ${BACKEND_RESULT} -eq 0 ]]; then
          echo "  ✓ Backend tests:  PASSED"
        else
          echo "  ✗ Backend tests:  FAILED"
        fi
        if [[ ${SECURITY_RESULT} -eq 0 ]]; then
          echo "  ✓ Security tests: PASSED"
        else
          echo "  ✗ Security tests: FAILED"
        fi
        if [[ ${OTHER_RESULT} -eq 0 ]]; then
          echo "  ✓ Other tests:    PASSED"
        else
          echo "  ✗ Other tests:    FAILED"
        fi
        if [[ -z "${BACKEND_ONLY}" ]]; then
          if [[ ${UI_RESULT} -eq 0 ]]; then
            echo "  ✓ UI tests:       PASSED"
          else
            echo "  ✗ UI tests:       FAILED"
          fi
        fi
        echo "========================================"
        if [[ -z "${ALL_MODE}" ]]; then
          echo ""
          echo "  NOTE: E2E tests were skipped. Use '--all' or '--only-e2e' to run them."
        fi
        echo ""
        echo "Test results saved to: ${TEST_LOG_FILE}"
      } | tee -a "$TEST_LOG_FILE"

      # Restore container to production mode
      echo ""
      echo "Restoring container to normal mode..."
      ${COMPOSE_CMD} up -d ag3ntum-api ag3ntum-web

      # Exit with error if any test suite failed
      if [[ ${BACKEND_RESULT} -ne 0 || ${SECURITY_RESULT} -ne 0 || ${OTHER_RESULT} -ne 0 || ${UI_RESULT} -ne 0 ]]; then
        echo "" | tee -a "$TEST_LOG_FILE"
        echo "Some tests failed!" | tee -a "$TEST_LOG_FILE"
        exit 1
      fi
      echo "" | tee -a "$TEST_LOG_FILE"
      echo "All tests passed!" | tee -a "$TEST_LOG_FILE"
      exit 0
    fi
  fi

  # Add default flags (only reached for --subset mode)
  PYTEST_ARGS+=("-v" "--tb=short")

  echo "Running: ${PYTEST_CMD} ${PYTEST_ARGS[*]}" | tee -a "$TEST_LOG_FILE"
  echo "" | tee -a "$TEST_LOG_FILE"

  # Run tests in container with logging
  run_with_log ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api ${PYTEST_CMD} "${PYTEST_ARGS[@]}"
  TEST_EXIT_CODE=$?

  # Print result summary
  {
    echo ""
    echo "========================================"
    if [[ ${TEST_EXIT_CODE} -eq 0 ]]; then
      echo "Tests PASSED"
    else
      echo "Tests FAILED"
    fi
    echo "========================================"
    echo ""
    echo "Test results saved to: ${TEST_LOG_FILE}"
  } | tee -a "$TEST_LOG_FILE"

  # Restore container to production mode (without test sudoers)
  echo ""
  echo "Restoring container to normal mode..."
  ${COMPOSE_CMD} up -d ag3ntum-api ag3ntum-web

  exit ${TEST_EXIT_CODE}
fi

# ---------------------------------------------------------------------------
# Dev environment helper: activate .venv if it exists (for lint/audit/setup)
# ---------------------------------------------------------------------------
VENV_DIR="${ROOT_DIR}/.venv"

activate_venv() {
  if [[ -d "${VENV_DIR}" ]]; then
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
    return 0
  fi
  return 1
}

# Handle setup action — install all dev tools (Python venv + Node + pre-commit)
if [[ "${ACTION}" == "setup" ]]; then
  echo "=== Setting up development environment ==="

  # 1. Check prerequisites
  if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found. Install Python 3.12+ first."
    exit 1
  fi
  if ! command -v node &>/dev/null; then
    echo "Error: node not found. Install Node.js 20+ first."
    exit 1
  fi

  PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  NODE_VERSION=$(node --version)
  echo "  Python: ${PYTHON_VERSION}"
  echo "  Node:   ${NODE_VERSION}"

  # 2. Create Python venv and install lint/audit tools
  echo ""
  echo "--- Python dev tools (.venv/) ---"
  if [[ ! -d "${VENV_DIR}" ]]; then
    echo "  Creating virtual environment..."
    python3 -m venv "${VENV_DIR}"
  else
    echo "  Virtual environment already exists."
  fi
  source "${VENV_DIR}/bin/activate"

  echo "  Installing Python dev tools..."
  pip install --quiet --upgrade pip
  pip install --quiet \
    flake8==7.3.0 \
    bandit==1.8.3 \
    mypy==1.14.1 \
    pip-audit==2.9.0 \
    pytest==9.0.2
  echo "  Installed: flake8, bandit, mypy, pip-audit, pytest"

  # 3. Install pre-commit hooks
  echo ""
  echo "--- Pre-commit hooks ---"
  pip install --quiet pre-commit
  if [[ -f ".pre-commit-config.yaml" ]]; then
    pre-commit install
    echo "  Pre-commit hooks installed."
  else
    echo "  Warning: .pre-commit-config.yaml not found, skipping hooks."
  fi

  # 4. Install frontend dependencies
  echo ""
  echo "--- Frontend dependencies (npm ci) ---"
  if [[ -f "src/web_terminal_client/package.json" ]]; then
    (cd src/web_terminal_client && npm ci --legacy-peer-deps 2>&1 | tail -3)
    echo "  Frontend dependencies installed."
  else
    echo "  Warning: src/web_terminal_client/package.json not found."
  fi

  echo ""
  echo "=== Dev environment ready ==="
  echo ""
  echo "Commands available:"
  echo "  ./run.sh lint    — run all linters"
  echo "  ./run.sh audit   — check dependency vulnerabilities"
  echo "  ./run.sh build   — build and start Docker containers"
  echo "  ./run.sh test    — run tests (requires Docker)"
  exit 0
fi

# Handle lint action (no Docker needed — runs on host)
if [[ "${ACTION}" == "lint" ]]; then
  # Auto-activate venv if available
  if ! activate_venv; then
    echo "Warning: .venv/ not found. Run './run.sh setup' first for full linting."
    echo "         Falling back to system tools..."
    echo ""
  fi

  echo "=== Running linters ==="
  LINT_EXIT=0

  # Python — flake8
  echo ""
  echo "--- flake8 (style) ---"
  if command -v flake8 &>/dev/null; then
    flake8 src/ tools/ tests/ --config=.flake8 || LINT_EXIT=1
  else
    echo "SKIPPED: flake8 not found. Run: ./run.sh setup"
    LINT_EXIT=1
  fi

  # Python — bandit (security)
  echo ""
  echo "--- bandit (security) ---"
  if command -v bandit &>/dev/null; then
    bandit -r src/ tools/ -c bandit.yaml -ll -ii || LINT_EXIT=1
  else
    echo "SKIPPED: bandit not found. Run: ./run.sh setup"
    LINT_EXIT=1
  fi

  # Structural tests
  echo ""
  echo "--- structural tests ---"
  if command -v pytest &>/dev/null || python3 -m pytest --version &>/dev/null 2>&1; then
    python3 -m pytest tests/structural/ -v --tb=long || LINT_EXIT=1
  else
    echo "SKIPPED: pytest not found. Run: ./run.sh setup"
    LINT_EXIT=1
  fi

  # Python — mypy (type checking, informational — not blocking until baseline established)
  echo ""
  echo "--- mypy (type checking — informational) ---"
  if command -v mypy &>/dev/null; then
    mypy src/core/ src/api/ src/services/ --config-file mypy.ini || echo "  (mypy found issues — informational, not blocking)"
  else
    echo "SKIPPED: mypy not found. Run: ./run.sh setup"
  fi

  # Frontend — TypeScript type check + ESLint
  if [ -d "src/web_terminal_client/node_modules" ]; then
    echo ""
    echo "--- TypeScript (tsc --noEmit — informational) ---"
    # Use tsconfig.lint.json — excludes test files (they need Docker node_modules)
    # Informational until pre-existing type errors are cleaned up (20 errors in existing code)
    (cd src/web_terminal_client && npx tsc --noEmit -p tsconfig.lint.json) || echo "  (tsc found type errors — informational, not blocking)"

    echo ""
    echo "--- ESLint (React/TypeScript) ---"
    (cd src/web_terminal_client && npx eslint src/ --max-warnings 53) || LINT_EXIT=1
  else
    echo ""
    echo "SKIPPED: Frontend linting (node_modules missing). Run: ./run.sh setup"
    LINT_EXIT=1
  fi

  echo ""
  if [[ ${LINT_EXIT} -eq 0 ]]; then
    echo "=== All lint checks passed ==="
  else
    echo "=== Some lint checks failed (exit code ${LINT_EXIT}) ==="
  fi
  exit ${LINT_EXIT}
fi

# Handle audit action (no Docker needed — runs on host)
if [[ "${ACTION}" == "audit" ]]; then
  # Auto-activate venv if available
  if ! activate_venv; then
    echo "Warning: .venv/ not found. Run './run.sh setup' first."
    echo ""
  fi

  echo "=== Running dependency audit ==="
  AUDIT_EXIT=0

  if command -v pip-audit &>/dev/null; then
    pip-audit --requirement requirements-base.txt --desc || AUDIT_EXIT=1
  else
    echo "SKIPPED: pip-audit not found. Run: ./run.sh setup"
    AUDIT_EXIT=1
  fi

  echo ""
  if [[ ${AUDIT_EXIT} -eq 0 ]]; then
    echo "=== Dependency audit passed ==="
  else
    echo "=== Dependency audit found issues (exit code ${AUDIT_EXIT}) ==="
  fi
  exit ${AUDIT_EXIT}
fi

# Handle shell action
if [[ "${ACTION}" == "shell" ]]; then
  echo "=== Opening shell in Docker container ==="

  # Check if container is running
  if ! ${COMPOSE_CMD} ps --status running --services 2>/dev/null | grep -q "ag3ntum-api"; then
    echo "Error: ag3ntum-api container is not running."
    echo "Start it first with: ./run.sh build"
    exit 1
  fi

  # Shell requires TTY
  if [ -t 0 ]; then
    ${COMPOSE_CMD} exec ag3ntum-api /bin/bash
  else
    echo "Error: Shell requires an interactive terminal."
    exit 1
  fi
  exit 0
fi

# Handle create-user action
if [[ "${ACTION}" == "create-user" ]]; then
  create_user ${TEST_ARGS[@]+"${TEST_ARGS[@]}"}
  exit 0
fi

# Handle delete-user action
if [[ "${ACTION}" == "delete-user" ]]; then
  delete_user ${TEST_ARGS[@]+"${TEST_ARGS[@]}"}
  exit 0
fi

# Handle cleanup-test-users action
if [[ "${ACTION}" == "cleanup-test-users" ]]; then
  echo "=== Cleaning up test users ==="

  # Use test compose override for cleanup (needs elevated permissions)
  COMPOSE_TEST="docker compose -f docker-compose.yml -f docker-compose.test.yml"
  EXEC_OPTS="-T -u ag3ntum_api"

  # Check if test configuration files exist
  if [[ ! -f "docker-compose.test.yml" ]] || [[ ! -f "config/test/sudoers-test" ]]; then
    echo "Warning: Test configuration files not found, using standard compose."
    echo "Some cleanup operations may fail without test permissions."
    COMPOSE_TEST="${COMPOSE_CMD}"
    EXEC_OPTS="-T"  # No user override needed for standard compose
  fi

  # Ensure container is running with test configuration for cleanup
  echo "Configuring container for cleanup..."
  ${COMPOSE_TEST} up -d ag3ntum-api
  sleep 2

  # Check if container is running
  if ! ${COMPOSE_TEST} ps --status running --services 2>/dev/null | grep -q "ag3ntum-api"; then
    echo "Error: ag3ntum-api container is not running."
    echo "Start it first with: ./run.sh build"
    exit 1
  fi

  # Run cleanup script inside container (run as ag3ntum_api to use test sudoers)
  ${COMPOSE_TEST} exec ${EXEC_OPTS} ag3ntum-api \
    python3 -m src.cli.cleanup_test_users ${TEST_ARGS[@]+"${TEST_ARGS[@]}"}

  # Restore container to production mode
  echo ""
  echo "Restoring container to normal mode..."
  ${COMPOSE_CMD} up -d ag3ntum-api ag3ntum-web

  exit 0
fi

# Handle rebuild action (cleanup + build)
if [[ "${ACTION}" == "rebuild" ]]; then
  do_cleanup
  ACTION="build"
  # Fall through to build
fi

# Validate and auto-provision config files before reading any config values
validate_and_provision_configs

API_PORT="$(read_config_value 'api.external_port' '40080')"
WEB_PORT="$(read_config_value 'web.external_port' '50080')"
REDIS_PORT="${AG3NTUM_REDIS_PORT:-46379}"

# Derive project name: preserve COMPOSE_PROJECT_NAME from .env if set (worktree instances),
# otherwise use directory basename (backward compatible — "project" for main)
if [[ -f .env ]] && grep -q '^COMPOSE_PROJECT_NAME=' .env; then
  PROJECT_NAME="$(grep '^COMPOSE_PROJECT_NAME=' .env | cut -d= -f2)"
else
  PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "$(pwd)" | tr '[:upper:]' '[:lower:]')}"
fi

# Setup directories with proper ownership before starting containers
# This ensures bind-mounted volumes are writable by the container user
setup_directories

# Load mounts from YAML config (before CLI args which can override)
load_mounts_from_yaml

render_ui_config
generate_compose_override

if [[ -f "${ROOT_DIR}/VERSION" ]]; then
  APP_VERSION="$(tr -d '[:space:]' < "${ROOT_DIR}/VERSION")"
else
  APP_VERSION="dev"
fi
IMAGE_TAG="${APP_VERSION}-$(date +%Y%m%d%H%M%S)"
BACKUP_ENV="$(mktemp)"
ROLLBACK_ENV=0

cleanup() {
  if [[ "${ROLLBACK_ENV}" -eq 1 && -s "${BACKUP_ENV}" ]]; then
    cp "${BACKUP_ENV}" .env
    ${COMPOSE_CMD} up -d --remove-orphans || true
  fi
  rm -f "${BACKUP_ENV}"
}

trap cleanup EXIT

if [[ -f .env ]]; then
  cp .env "${BACKUP_ENV}"
fi

echo "Building image ag3ntum:${IMAGE_TAG}..."
if [[ -n "${NO_CACHE}" ]]; then
  echo "  (Using --no-cache for fresh build)"
fi
docker build ${NO_CACHE} --build-arg APP_VERSION="${APP_VERSION}" -t "ag3ntum:${IMAGE_TAG}" .

ROLLBACK_ENV=1
cat > .env <<EOF
AG3NTUM_IMAGE_TAG=${IMAGE_TAG}
AG3NTUM_API_PORT=${API_PORT}
AG3NTUM_WEB_PORT=${WEB_PORT}
AG3NTUM_REDIS_PORT=${REDIS_PORT}
AG3NTUM_MODE=${AG3NTUM_MODE}
COMPOSE_PROJECT_NAME=${PROJECT_NAME}
EOF

echo "Starting containers with tag ${IMAGE_TAG} (mode: ${AG3NTUM_MODE})..."
# Use --force-recreate to ensure fresh containers with new code
${COMPOSE_CMD} up -d --remove-orphans --force-recreate

if ! check_services; then
  echo "Deployment failed, rolling back."
  exit 1
fi

ROLLBACK_ENV=0

# Validate frontend build (catches module resolution failures early)
echo ""
if [[ "${AG3NTUM_MODE}" == "dev" ]]; then
  # Dev mode: validate via web container's Vite build
  if ${COMPOSE_CMD} ps --status running --services 2>/dev/null | grep -q "ag3ntum-web"; then
    echo "Validating frontend build (dev mode)..."
    if ${COMPOSE_CMD} exec -T ag3ntum-web sh -c \
      'cd /src/web_terminal_client && vite build --config /tmp/vite-${AG3NTUM_WEB_PORT:-50080}/vite.config.mjs' \
      >/dev/null 2>&1; then
      echo "  Frontend build validation passed"
    else
      echo "  WARNING: Frontend build validation failed. Check web container logs."
      echo "           ${COMPOSE_CMD} logs ag3ntum-web"
    fi
  fi
else
  # Prod mode: verify the static bundle exists in the web container
  echo "Validating production frontend bundle..."
  if ${COMPOSE_CMD} exec -T ag3ntum-web sh -c 'test -f /web_dist/index.html' 2>/dev/null; then
    echo "  Production frontend bundle verified"
  else
    echo "  WARNING: Production frontend bundle missing. Check Dockerfile build stage."
  fi
fi

# Verify fresh containers
echo ""
echo "=== Deployment Verification ==="
echo "Instance:   ${PROJECT_NAME}"
echo "Mode:       ${AG3NTUM_MODE}"
echo "Image tag:  ${IMAGE_TAG}"
echo "Web Port:   ${WEB_PORT}"
echo "API Port:   ${API_PORT}"
echo "Redis Port: ${REDIS_PORT}"
echo ""
echo "Container status:"
${COMPOSE_CMD} ps
echo ""
echo "Web UI:  http://localhost:${WEB_PORT}"
echo "API:     http://localhost:${API_PORT}"
echo ""
echo "=== Deployment complete at $(date) ==="
