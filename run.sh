#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# Arrays for mount options (format: "path:name")
MOUNTS_RW=()
MOUNTS_RO=()
MOUNTS_USER_RW=()
MOUNTS_USER_RO=()

# Track used mount names to detect duplicates (space-separated string for Bash 3 compat)
USED_MOUNT_NAMES=""

# Configuration
PROJECT_NAME="project"  # docker-compose project name
IMAGE_PREFIX="ag3ntum"  # Image name prefix

# Reserved mount names that cannot be used
RESERVED_NAMES=("persistent" "ro" "rw" "external")

function show_usage() {
  cat <<EOF
Usage: ./run.sh <command> [OPTIONS]

Commands:
  build              Build and deploy the containers
  cleanup            Stop containers and remove images (full cleanup)
  restart            Restart containers to reload code (preserves data)
  rebuild            Full cleanup + build (equivalent to: cleanup && build)
  test               Run tests inside the Docker container
  shell              Open a shell inside the API container
  create-user        Create a new user account
  cleanup-test-users Remove test users created during testing

Options:
  --mount-rw=PATH:NAME  Mount host PATH as read-write (accessible at ./external/rw/NAME)
  --mount-rw=PATH       Mount host PATH as read-write (name defaults to basename)
  --mount-ro=PATH:NAME  Mount host PATH as read-only (accessible at ./external/ro/NAME)
  --mount-ro=PATH       Mount host PATH as read-only (name defaults to basename)
  --no-cache            Force rebuild without Docker cache (for build/rebuild)
  --help                Show this help message

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

CLI Hints:
  View logs:     docker compose logs -f ag3ntum-api
  API health:    curl http://localhost:40080/api/v1/health
  Redis CLI:     docker exec -it project-redis-1 redis-cli
  Stop all:      docker compose down
EOF
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
    fi
  done <<< "${mounts_output}"
}

# Parse arguments
ACTION=""
NO_CACHE=""
TEST_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    build|cleanup|restart|rebuild|test|shell|create-user|cleanup-test-users)
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

function read_config_value() {
  local key="$1"
  local config_file="config/api.yaml"

  # Split key into section and field (e.g., "api.external_port" -> "api" "external_port")
  local section="${key%%.*}"
  local field="${key##*.}"

  # Parse simple nested YAML without external dependencies
  # Handles format:  section:
  #                    field: value
  awk -v section="$section" -v field="$field" '
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
  ' "$config_file"
}

function render_ui_config() {
  cat > src/web_terminal_client/public/config.yaml <<EOF
server:
  port: ${WEB_PORT}
  host: "0.0.0.0"

api:
  base_url: "http://localhost:${API_PORT}"

ui:
  max_output_lines: 1000
  auto_scroll: true
EOF
}

function generate_compose_override() {
  # Generate docker-compose.override.yml with extra mounts if any were specified
  local override_file="docker-compose.override.yml"
  local manifest_file="data/auto-generated/auto-generated-mounts.yaml"

  # Ensure the auto-generated directory exists
  mkdir -p "data/auto-generated"

  if [[ ${#MOUNTS_RW[@]} -eq 0 && ${#MOUNTS_RO[@]} -eq 0 && ${#MOUNTS_USER_RW[@]} -eq 0 && ${#MOUNTS_USER_RO[@]} -eq 0 ]]; then
    # No mounts specified, remove override file and create empty manifest
    rm -f "${override_file}"
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
#   Persistent: /workspace/external/persistent/
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

  # Write RO section
  if [[ ${#MOUNTS_RO[@]} -gt 0 ]]; then
    echo "  ro:" >> "${manifest_file}"
    for mount in "${MOUNTS_RO[@]}"; do
      local abs_path="${mount%%:*}"
      local name="${mount##*:}"
      echo "      - ${abs_path}:/mounts/ro/${name}:ro" >> "${override_file}"
      echo "    - name: \"${name}\"" >> "${manifest_file}"
      echo "      host_path: \"${abs_path}\"" >> "${manifest_file}"
      echo "      container_path: \"/mounts/ro/${name}\"" >> "${manifest_file}"
      echo "      workspace_path: \"./external/ro/${name}\"" >> "${manifest_file}"
    done
  else
    echo "  ro: []" >> "${manifest_file}"
  fi

  # Write RW section
  if [[ ${#MOUNTS_RW[@]} -gt 0 ]]; then
    echo "  rw:" >> "${manifest_file}"
    for mount in "${MOUNTS_RW[@]}"; do
      local abs_path="${mount%%:*}"
      local name="${mount##*:}"
      echo "      - ${abs_path}:/mounts/rw/${name}:rw" >> "${override_file}"
      echo "    - name: \"${name}\"" >> "${manifest_file}"
      echo "      host_path: \"${abs_path}\"" >> "${manifest_file}"
      echo "      container_path: \"/mounts/rw/${name}\"" >> "${manifest_file}"
      echo "      workspace_path: \"./external/rw/${name}\"" >> "${manifest_file}"
    done
  else
    echo "  rw: []" >> "${manifest_file}"
  fi

  # Write per-user RO section (mounted at /mounts/user-ro/{name})
  if [[ ${#MOUNTS_USER_RO[@]} -gt 0 ]]; then
    echo "  user-ro:" >> "${manifest_file}"
    for mount in "${MOUNTS_USER_RO[@]}"; do
      local abs_path="${mount%%:*}"
      local name="${mount##*:}"
      echo "      - ${abs_path}:/mounts/user-ro/${name}:ro" >> "${override_file}"
      echo "    - name: \"${name}\"" >> "${manifest_file}"
      echo "      host_path: \"${abs_path}\"" >> "${manifest_file}"
      echo "      container_path: \"/mounts/user-ro/${name}\"" >> "${manifest_file}"
      echo "      workspace_path: \"./external/user-ro/${name}\"" >> "${manifest_file}"
    done
  else
    echo "  user-ro: []" >> "${manifest_file}"
  fi

  # Write per-user RW section (mounted at /mounts/user-rw/{name})
  if [[ ${#MOUNTS_USER_RW[@]} -gt 0 ]]; then
    echo "  user-rw:" >> "${manifest_file}"
    for mount in "${MOUNTS_USER_RW[@]}"; do
      local abs_path="${mount%%:*}"
      local name="${mount##*:}"
      echo "      - ${abs_path}:/mounts/user-rw/${name}:rw" >> "${override_file}"
      echo "    - name: \"${name}\"" >> "${manifest_file}"
      echo "      host_path: \"${abs_path}\"" >> "${manifest_file}"
      echo "      container_path: \"/mounts/user-rw/${name}\"" >> "${manifest_file}"
      echo "      workspace_path: \"./external/user-rw/${name}\"" >> "${manifest_file}"
    done
  else
    echo "  user-rw: []" >> "${manifest_file}"
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
  echo "Persistent storage (always available):"
  echo "  ./external/persistent/"
  echo ""
}

function check_services() {
  local missing=0
  local running
  running="$(docker compose ps --status running --services || true)"
  for svc in ag3ntum-api ag3ntum-web; do
    if ! grep -q "${svc}" <<<"${running}"; then
      echo "Service not running: ${svc}"
      missing=1
    fi
  done
  return "${missing}"
}

function do_cleanup() {
  echo "=== Starting comprehensive cleanup ==="
  
  # Step 1: Stop and remove all project containers (including stuck ones)
  echo "Stopping containers..."
  docker compose down --remove-orphans --timeout 10 2>/dev/null || true
  
  # Step 2: Force kill any stuck containers by name pattern
  echo "Force removing any stuck containers..."
  local stuck_containers
  stuck_containers=$(docker ps -aq --filter "name=${PROJECT_NAME}" 2>/dev/null || true)
  if [[ -n "${stuck_containers}" ]]; then
    echo "  Found stuck containers: ${stuck_containers}"
    echo "${stuck_containers}" | xargs -r docker rm -f 2>/dev/null || true
  fi
  
  # Also check for containers using ag3ntum images
  stuck_containers=$(docker ps -aq --filter "ancestor=${IMAGE_PREFIX}" 2>/dev/null || true)
  if [[ -n "${stuck_containers}" ]]; then
    echo "  Found containers using ${IMAGE_PREFIX} images: ${stuck_containers}"
    echo "${stuck_containers}" | xargs -r docker rm -f 2>/dev/null || true
  fi
  
  # Step 3: Remove all ag3ntum images (all tags)
  echo "Removing ${IMAGE_PREFIX} images..."
  local images
  images=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep "^${IMAGE_PREFIX}:" || true)
  if [[ -n "${images}" ]]; then
    echo "  Removing: ${images}"
    echo "${images}" | xargs -r docker rmi -f 2>/dev/null || true
  fi
  
  # Also remove any dangling images that might be leftovers from builds
  local dangling
  dangling=$(docker images -q --filter "dangling=true" 2>/dev/null || true)
  if [[ -n "${dangling}" ]]; then
    echo "  Removing dangling images..."
    echo "${dangling}" | xargs -r docker rmi -f 2>/dev/null || true
  fi
  
  # Step 4: Remove project-specific volumes (but NOT data volumes)
  # Only remove the web_node_modules volume, preserve data
  echo "Cleaning build volumes..."
  docker volume rm -f "${PROJECT_NAME}_web_node_modules" 2>/dev/null || true

  # Step 5: Clean up project-specific networks only (not all networks)
  echo "Cleaning project networks..."
  local project_networks
  project_networks=$(docker network ls --filter "name=${PROJECT_NAME}" -q 2>/dev/null || true)
  if [[ -n "${project_networks}" ]]; then
    echo "  Removing networks: ${project_networks}"
    echo "${project_networks}" | xargs -r docker network rm 2>/dev/null || true
  fi
  
  # Step 6: Remove generated files
  echo "Removing generated files..."
  rm -f docker-compose.override.yml
  rm -f .env.bak

  # Step 7: Kill any orphaned processes that might be using ports
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
  echo "=== Restarting containers to reload code ==="
  
  # Restart API container (where Python code runs)
  echo "Restarting ag3ntum-api..."
  docker compose restart ag3ntum-api
  
  # Optionally restart web if needed
  echo "Restarting ag3ntum-web..."
  docker compose restart ag3ntum-web
  
  # Wait for services to be healthy
  sleep 2
  
  if check_services; then
    echo "=== Restart complete - services running ==="
  else
    echo "=== WARNING: Some services may not be running ==="
    docker compose ps
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
    exit 1
  fi

  # Check if container is running
  if ! docker compose ps --status running --services 2>/dev/null | grep -q "ag3ntum-api"; then
    echo "Error: ag3ntum-api container is not running."
    echo "Start it first with: ./run.sh build"
    exit 1
  fi

  echo "=== Creating user: $USERNAME ==="

  # Run create_user.py inside container
  docker compose -p "${PROJECT_NAME}" exec ag3ntum-api \
    python3 src/cli/create_user.py \
    --username="$USERNAME" \
    --email="$EMAIL" \
    --password="$PASSWORD" \
    $ADMIN
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

  local WEB_CLIENT_DIR="${ROOT_DIR}/src/web_terminal_client"

  # Check if web_terminal_client directory exists
  if [[ ! -d "${WEB_CLIENT_DIR}" ]]; then
    echo "Error: web_terminal_client directory not found at ${WEB_CLIENT_DIR}"
    return 1
  fi

  # Check if node_modules exists
  if [[ ! -d "${WEB_CLIENT_DIR}/node_modules" ]]; then
    echo "Installing npm dependencies..."
    (cd "${WEB_CLIENT_DIR}" && npm install)
  fi

  # Run vitest
  echo "Running vitest..."
  (cd "${WEB_CLIENT_DIR}" && npm run test:run)
  return $?
}

# Handle test action
if [[ "${ACTION}" == "test" ]]; then
  echo "=== Running tests ==="

  # Build pytest command
  PYTEST_CMD="python -m pytest"

  # Parse test arguments
  QUICK_MODE=""
  SUBSET=""
  BACKEND_ONLY=""
  UI_ONLY=""

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
      --subset)
        ((i++))
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
        echo "Usage: ./run.sh test [--quick] [--backend] [--ui] [--subset <names>]"
        exit 1
        ;;
    esac
    ((i++))
  done

  # Handle UI-only mode
  if [[ -n "${UI_ONLY}" ]]; then
    run_ui_tests
    exit $?
  fi

  # For backend tests, check if container is running
  if ! docker compose ps --status running --services 2>/dev/null | grep -q "ag3ntum-api"; then
    echo "Error: ag3ntum-api container is not running."
    echo "Start it first with: ./run.sh build"
    exit 1
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
      # Find matching test files in container
      MATCHES=$(docker exec "${PROJECT_NAME}-ag3ntum-api-1" find /tests -name "test_*${name}*.py" 2>/dev/null | sort -u)
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
      docker exec "${PROJECT_NAME}-ag3ntum-api-1" find /tests -name "test_*.py" | sort
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

      # Run backend tests in container (use -t only if TTY available)
      BACKEND_RESULT=0
      if [[ -z "${UI_ONLY}" ]]; then
        if [ -t 0 ]; then
          docker exec -it "${PROJECT_NAME}-ag3ntum-api-1" ${PYTEST_CMD} "${PYTEST_ARGS[@]}"
        else
          docker exec "${PROJECT_NAME}-ag3ntum-api-1" ${PYTEST_CMD} "${PYTEST_ARGS[@]}"
        fi
        BACKEND_RESULT=$?
      fi

      # Run UI tests unless backend-only
      UI_RESULT=0
      if [[ -z "${BACKEND_ONLY}" ]]; then
        echo ""
        run_ui_tests
        UI_RESULT=$?
      fi

      # Print summary for quick mode
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

      if [[ ${BACKEND_RESULT} -ne 0 || ${UI_RESULT} -ne 0 ]]; then
        exit 1
      fi
      exit 0
    else
      # Full mode: run backend tests with --run-e2e, then other tests without it
      echo "Running ALL tests (backend with E2E + security + other tests)..."
      echo ""

      # First run: backend tests with --run-e2e flag
      echo "=== Running backend tests (with E2E) ==="
      if [ -t 0 ]; then
        docker exec -it "${PROJECT_NAME}-ag3ntum-api-1" ${PYTEST_CMD} tests/backend/ --run-e2e -v --tb=short
      else
        docker exec "${PROJECT_NAME}-ag3ntum-api-1" ${PYTEST_CMD} tests/backend/ --run-e2e -v --tb=short
      fi
      BACKEND_RESULT=$?

      # Second run: security tests (no --run-e2e flag)
      echo ""
      echo "=== Running security tests ==="
      if [ -t 0 ]; then
        docker exec -it "${PROJECT_NAME}-ag3ntum-api-1" ${PYTEST_CMD} tests/security/ -v --tb=short
      else
        docker exec "${PROJECT_NAME}-ag3ntum-api-1" ${PYTEST_CMD} tests/security/ -v --tb=short
      fi
      SECURITY_RESULT=$?

      # Check for other test directories and run them
      OTHER_DIRS=$(docker exec "${PROJECT_NAME}-ag3ntum-api-1" find /tests -maxdepth 1 -type d ! -name backend ! -name security ! -name __pycache__ ! -name tests 2>/dev/null | grep -v "^/tests$" || true)
      OTHER_RESULT=0

      if [[ -n "${OTHER_DIRS}" ]]; then
        for dir in ${OTHER_DIRS}; do
          dir_name=$(basename "${dir}")
          if [[ "${dir_name}" != ".DS_Store" && "${dir_name}" != "__pycache__" ]]; then
            # Check if directory has any test files
            HAS_TESTS=$(docker exec "${PROJECT_NAME}-ag3ntum-api-1" find "${dir}" -name "test_*.py" 2>/dev/null | head -1)
            if [[ -n "${HAS_TESTS}" ]]; then
              echo ""
              echo "=== Running ${dir_name} tests ==="
              if [ -t 0 ]; then
                docker exec -it "${PROJECT_NAME}-ag3ntum-api-1" ${PYTEST_CMD} "${dir}/" -v --tb=short
              else
                docker exec "${PROJECT_NAME}-ag3ntum-api-1" ${PYTEST_CMD} "${dir}/" -v --tb=short
              fi
              if [[ $? -ne 0 ]]; then
                OTHER_RESULT=1
              fi
            fi
          fi
        done
      fi

      # Run UI tests if not backend-only mode
      UI_RESULT=0
      if [[ -z "${BACKEND_ONLY}" ]]; then
        echo ""
        run_ui_tests
        UI_RESULT=$?
      fi

      # Print combined summary
      echo ""
      echo "========================================"
      echo "=== COMBINED TEST SUMMARY ==="
      echo "========================================"
      TOTAL_BACKEND=$(docker exec "${PROJECT_NAME}-ag3ntum-api-1" python -m pytest tests/ --collect-only -q 2>/dev/null | tail -1 | grep -oE '[0-9]+' | head -1)
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

      # Exit with error if any test suite failed
      if [[ ${BACKEND_RESULT} -ne 0 || ${SECURITY_RESULT} -ne 0 || ${OTHER_RESULT} -ne 0 || ${UI_RESULT} -ne 0 ]]; then
        echo ""
        echo "Some tests failed!"
        exit 1
      fi
      echo ""
      echo "All tests passed!"
      exit 0
    fi
  fi

  # Add default flags (only reached for --subset mode)
  PYTEST_ARGS+=("-v" "--tb=short")

  echo "Running: ${PYTEST_CMD} ${PYTEST_ARGS[*]}"
  echo ""

  # Run tests in container (use -t only if TTY available)
  if [ -t 0 ]; then
    docker exec -it "${PROJECT_NAME}-ag3ntum-api-1" ${PYTEST_CMD} "${PYTEST_ARGS[@]}"
  else
    docker exec "${PROJECT_NAME}-ag3ntum-api-1" ${PYTEST_CMD} "${PYTEST_ARGS[@]}"
  fi
  exit $?
fi

# Handle shell action
if [[ "${ACTION}" == "shell" ]]; then
  echo "=== Opening shell in Docker container ==="

  # Check if container is running
  if ! docker compose ps --status running --services 2>/dev/null | grep -q "ag3ntum-api"; then
    echo "Error: ag3ntum-api container is not running."
    echo "Start it first with: ./run.sh build"
    exit 1
  fi

  # Shell requires TTY
  if [ -t 0 ]; then
    docker exec -it "${PROJECT_NAME}-ag3ntum-api-1" /bin/bash
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

# Handle cleanup-test-users action
if [[ "${ACTION}" == "cleanup-test-users" ]]; then
  echo "=== Cleaning up test users ==="

  # Check if container is running
  if ! docker ps --format '{{.Names}}' | grep -q "^${PROJECT_NAME}-ag3ntum-api-1$"; then
    echo "Error: Container ${PROJECT_NAME}-ag3ntum-api-1 is not running."
    echo "Start it first with: ./run.sh build"
    exit 1
  fi

  # Run cleanup script inside container
  if [ -t 0 ]; then
    docker exec -it "${PROJECT_NAME}-ag3ntum-api-1" \
      python3 -m src.cli.cleanup_test_users ${TEST_ARGS[@]+"${TEST_ARGS[@]}"}
  else
    docker exec "${PROJECT_NAME}-ag3ntum-api-1" \
      python3 -m src.cli.cleanup_test_users ${TEST_ARGS[@]+"${TEST_ARGS[@]}"}
  fi
  exit 0
fi

# Handle rebuild action (cleanup + build)
if [[ "${ACTION}" == "rebuild" ]]; then
  do_cleanup
  ACTION="build"
  # Fall through to build
fi

API_PORT="$(read_config_value 'api.external_port')"
WEB_PORT="$(read_config_value 'web.external_port')"

# Load mounts from YAML config (before CLI args which can override)
load_mounts_from_yaml

render_ui_config
generate_compose_override

IMAGE_TAG="deploy-$(date +%Y%m%d%H%M%S)"
BACKUP_ENV="$(mktemp)"
ROLLBACK_ENV=0

cleanup() {
  if [[ "${ROLLBACK_ENV}" -eq 1 && -s "${BACKUP_ENV}" ]]; then
    cp "${BACKUP_ENV}" .env
    docker compose up -d --remove-orphans || true
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
docker build ${NO_CACHE} -t "ag3ntum:${IMAGE_TAG}" .

ROLLBACK_ENV=1
cat > .env <<EOF
AG3NTUM_IMAGE_TAG=${IMAGE_TAG}
AG3NTUM_API_PORT=${API_PORT}
AG3NTUM_WEB_PORT=${WEB_PORT}
EOF

echo "Starting containers with tag ${IMAGE_TAG}..."
# Use --force-recreate to ensure fresh containers with new code
docker compose up -d --remove-orphans --force-recreate

if ! check_services; then
  echo "Deployment failed, rolling back."
  exit 1
fi

ROLLBACK_ENV=0

# Verify fresh containers
echo ""
echo "=== Deployment Verification ==="
echo "Image tag: ${IMAGE_TAG}"
echo "API Port: ${API_PORT}"
echo "Web Port: ${WEB_PORT}"
echo ""
echo "Container status:"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Image}}"
echo ""
echo "Container start times:"
for container in "${PROJECT_NAME}-ag3ntum-api-1" "${PROJECT_NAME}-ag3ntum-web-1"; do
  started=$(docker inspect "${container}" --format '{{.State.StartedAt}}' 2>/dev/null || echo "N/A")
  echo "  ${container}: ${started}"
done
echo ""
echo "=== Deployment complete at $(date) ==="
