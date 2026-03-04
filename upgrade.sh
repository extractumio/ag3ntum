#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${ROOT_DIR}"

# =============================================================================
# COLORS
# =============================================================================

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
BLUE=$'\033[0;34m'
CYAN=$'\033[0;36m'
WHITE=$'\033[0;37m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
NC=$'\033[0m'

# =============================================================================
# GLOBALS
# =============================================================================

DRY_RUN=0
FORCE=0
SKIP_BACKUP=0
MODE="upgrade"           # upgrade | rollback | check

CURRENT_VERSION=""
TARGET_VERSION=""
STAGE=""                 # used by handle_interrupt

NEEDS_NO_CACHE=0

HASH_REQUIREMENTS=""
HASH_PACKAGE_JSON=""
HASH_DOCKERFILE=""

BACKUPS_DIR="${ROOT_DIR}/backups"
API_PORT="${API_PORT:-40080}"

# =============================================================================
# PRINT HELPERS
# =============================================================================

print_info() {
    printf "${CYAN}i${NC} %s\n" "$1"
}

print_success() {
    printf "${GREEN}ok${NC} %s\n" "$1"
}

print_warning() {
    printf "${YELLOW}warn${NC} %s\n" "$1"
}

print_error() {
    printf "${RED}error${NC} %s\n" "$1" >&2
}

print_step() {
    printf "\n${BOLD}${BLUE}>>${NC} ${BOLD}%s${NC}\n" "$1"
}

print_dim() {
    printf "${DIM}%s${NC}\n" "$1"
}

confirm_or_exit() {
    # Usage: confirm_or_exit "Prompt text" "Cancel message"
    local prompt="$1"
    local cancel_msg="$2"
    printf "${YELLOW}?${NC} %s [y/N]: " "${prompt}"
    local answer
    read -r answer < /dev/tty
    if [[ "${answer}" != "y" && "${answer}" != "Y" ]]; then
        print_info "${cancel_msg}"
        exit 0
    fi
}

_get_hash_cmd() {
    # Detect available hash command. Sets HASH_CMD global.
    if command -v sha256sum > /dev/null 2>&1; then
        HASH_CMD="sha256sum"
    elif command -v shasum > /dev/null 2>&1; then
        HASH_CMD="shasum -a 256"
    else
        HASH_CMD=""
    fi
}

# =============================================================================
# SHOW HELP
# =============================================================================

show_help() {
    cat <<EOF
${BOLD}Usage:${NC} ./upgrade.sh [OPTIONS]

Upgrade Ag3ntum to the latest version from the remote repository.

${BOLD}Options:${NC}
  --dry-run       Show what would happen without making changes.
                  Code is pulled and inspected; no services are stopped,
                  no build is run, and no data is modified.

  --force         Skip confirmation prompts and active-session warnings.
                  Use for automated / unattended upgrades.

  --skip-backup   Skip the backup step. Suitable for CI/CD pipelines
                  where backups are managed externally.

  --rollback      Restore from the most recent backup found in backups/.
                  Stops services, restores data and config, rebuilds.

  --check         Run health diagnostics only. No changes are made.
                  Shows versions, service status, database, disk space,
                  and recent backups.

  --help          Show this help message.

${BOLD}Examples:${NC}
  ./upgrade.sh                     # Interactive upgrade with confirmation
  ./upgrade.sh --dry-run           # Preview what the upgrade would do
  ./upgrade.sh --force             # Unattended upgrade (no prompts)
  ./upgrade.sh --skip-backup       # CI/CD upgrade without backup
  ./upgrade.sh --rollback          # Restore previous version
  ./upgrade.sh --check             # Health check only

${BOLD}Backup location:${NC}  ${ROOT_DIR}/backups/
${BOLD}Version file:${NC}     ${ROOT_DIR}/VERSION
${BOLD}Installed marker:${NC} ${ROOT_DIR}/.ag3ntum-version
EOF
}

# =============================================================================
# ARGUMENT PARSING
# =============================================================================

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)    DRY_RUN=1 ;;
            --force)      FORCE=1 ;;
            --skip-backup) SKIP_BACKUP=1 ;;
            --rollback)   MODE="rollback" ;;
            --check)      MODE="check" ;;
            --help|-h)    show_help; exit 0 ;;
            *)
                print_error "Unknown option: $1"
                echo "Run './upgrade.sh --help' for usage."
                exit 1
                ;;
        esac
        shift
    done
}

# =============================================================================
# VERSION HELPERS
# =============================================================================

get_current_version() {
    if [[ -f "${ROOT_DIR}/.ag3ntum-version" ]]; then
        tr -d '[:space:]' < "${ROOT_DIR}/.ag3ntum-version"
    elif [[ -f "${ROOT_DIR}/VERSION" ]]; then
        tr -d '[:space:]' < "${ROOT_DIR}/VERSION"
    else
        echo "0.0.0"
    fi
}

# Parse semver into parts. Sets globals VER_MAJOR VER_MINOR VER_PATCH.
parse_version() {
    local ver="$1"
    # Strip leading 'v' if present
    ver="${ver#v}"
    IFS='.' read -r VER_MAJOR VER_MINOR VER_PATCH <<< "${ver}"
    VER_MAJOR="${VER_MAJOR:-0}"
    VER_MINOR="${VER_MINOR:-0}"
    VER_PATCH="${VER_PATCH:-0}"
}

# =============================================================================
# CHECK PREREQUISITES
# =============================================================================

check_prerequisites() {
    print_step "Checking prerequisites"

    # Must be in a git repo
    if ! git rev-parse --git-dir > /dev/null 2>&1; then
        print_error "Not inside a git repository. Cannot upgrade."
        exit 1
    fi
    print_success "Git repository detected"

    # Must have run.sh
    if [[ ! -f "${ROOT_DIR}/run.sh" ]]; then
        print_error "run.sh not found in ${ROOT_DIR}. Is this the correct project directory?"
        exit 1
    fi

    # Must have VERSION
    if [[ ! -f "${ROOT_DIR}/VERSION" ]]; then
        print_error "VERSION file not found in ${ROOT_DIR}."
        exit 1
    fi
    print_success "run.sh and VERSION present"

    # Docker daemon
    if ! docker info > /dev/null 2>&1; then
        print_error "Docker daemon is not running. Start Docker and try again."
        exit 1
    fi
    print_success "Docker daemon is running"

    # docker compose (v2 plugin)
    if ! docker compose version > /dev/null 2>&1; then
        print_error "'docker compose' (v2) is not available. Install the Docker Compose plugin."
        exit 1
    fi
    print_success "docker compose v2 available"

    # Disk space: at least 500 MB free in project dir
    local free_kb
    free_kb=$(df -k "${ROOT_DIR}" | awk 'NR==2 {print $4}')
    local free_mb=$(( free_kb / 1024 ))
    if [[ "${free_mb}" -lt 500 ]]; then
        print_error "Insufficient disk space: ${free_mb} MB free (need at least 500 MB)."
        exit 1
    fi
    print_success "Disk space: ${free_mb} MB free"
}

# =============================================================================
# CHECK ACTIVE SESSIONS
# =============================================================================

check_active_sessions() {
    print_step "Checking for active agent sessions"

    local active_count=0

    # Try to query Redis via the API container for active tasks.
    # If the container is not running, we treat it as 0 active sessions.
    if docker compose ps --services --filter "status=running" 2>/dev/null | grep -q "ag3ntum-api"; then
        active_count=$(
            docker compose exec -T ag3ntum-api python3 - <<'PYEOF' 2>/dev/null || echo "0"
import sys
try:
    import redis, os
    r = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://ag3ntum-redis:6379/0"))
    # Active tasks are stored as members of the running set in the task queue
    keys = r.keys("ag3ntum:task:*:status")
    active = sum(1 for k in keys if r.get(k) in (b"running", b"queued"))
    print(active)
except Exception:
    print(0)
PYEOF
        )
        active_count="${active_count//[^0-9]/}"
        active_count="${active_count:-0}"
    else
        print_dim "  API container not running — skipping session check"
        return 0
    fi

    if [[ "${active_count}" -gt 0 ]]; then
        print_warning "${active_count} active agent session(s) detected."
        print_warning "Upgrading now will interrupt running tasks."
        if [[ "${FORCE}" -eq 0 ]]; then
            confirm_or_exit "Continue anyway?" \
                "Upgrade cancelled. Wait for sessions to finish or use --force."
        else
            print_warning "--force specified. Proceeding despite active sessions."
        fi
    else
        print_success "No active agent sessions"
    fi
}

# =============================================================================
# CREATE BACKUP
# =============================================================================

create_backup() {
    if [[ "${SKIP_BACKUP}" -eq 1 ]]; then
        print_dim "  [--skip-backup] Skipping backup."
        return 0
    fi

    print_step "Creating backup"

    mkdir -p "${BACKUPS_DIR}"

    local timestamp
    timestamp=$(date +%Y%m%d-%H%M%S)
    local archive="${BACKUPS_DIR}/upgrade-${CURRENT_VERSION}-${timestamp}.tar.gz"

    # Build list of items to back up (skip missing items gracefully)
    local items=()
    [[ -d "${ROOT_DIR}/data" ]]             && items+=("data")
    [[ -d "${ROOT_DIR}/config" ]]           && items+=("config")
    [[ -f "${ROOT_DIR}/.env" ]]             && items+=(".env")
    [[ -f "${ROOT_DIR}/.ag3ntum-version" ]] && items+=(".ag3ntum-version")

    if [[ ${#items[@]} -eq 0 ]]; then
        print_warning "Nothing to back up (data/, config/, .env not found). Skipping."
        return 0
    fi

    print_info "Archiving: ${items[*]}"

    (
        cd "${ROOT_DIR}"
        tar -czf "${archive}" "${items[@]}" 2>/dev/null
    )

    chmod 600 "${archive}"
    print_success "Backup created: ${archive}"

    # Auto-prune: keep only the 3 most recent backups
    local count
    count=$(find "${BACKUPS_DIR}" -maxdepth 1 -name "upgrade-*.tar.gz" | wc -l)
    if [[ "${count}" -gt 3 ]]; then
        print_dim "  Pruning old backups (keeping 3 most recent)..."
        # Sort by modification time, remove oldest
        find "${BACKUPS_DIR}" -maxdepth 1 -name "upgrade-*.tar.gz" \
            -printf '%T@ %p\n' 2>/dev/null \
            | sort -n \
            | head -n $(( count - 3 )) \
            | awk '{print $2}' \
            | xargs -r rm -f
        print_success "Old backups pruned"
    fi
}

# =============================================================================
# CHECK VERSION COMPATIBILITY
# =============================================================================

check_version_compatibility() {
    print_step "Checking version compatibility"

    local cur="${CURRENT_VERSION}"
    local tgt="${TARGET_VERSION}"

    print_info "Current: ${cur}  →  Target: ${tgt}"

    if [[ -z "${tgt}" || "${tgt}" == "${cur}" ]]; then
        print_info "Already at version ${cur}. Nothing to upgrade."
        if [[ "${FORCE}" -eq 0 ]]; then
            exit 0
        fi
        print_warning "--force specified. Continuing anyway."
        return 0
    fi

    # Parse versions
    local cur_major cur_minor cur_patch
    local tgt_major tgt_minor tgt_patch

    parse_version "${cur}"
    cur_major="${VER_MAJOR}"; cur_minor="${VER_MINOR}"; cur_patch="${VER_PATCH}"

    parse_version "${tgt}"
    tgt_major="${VER_MAJOR}"; tgt_minor="${VER_MINOR}"; tgt_patch="${VER_PATCH}"

    # Downgrade detection
    if (( tgt_major < cur_major )) || \
       (( tgt_major == cur_major && tgt_minor < cur_minor )) || \
       (( tgt_major == cur_major && tgt_minor == cur_minor && tgt_patch < cur_patch )); then
        print_error "Downgrade detected: ${cur} → ${tgt}"
        print_error "Downgrades are not supported. Use --rollback to restore a backup."
        if [[ "${FORCE}" -eq 0 ]]; then
            exit 1
        fi
        print_warning "--force specified. Proceeding with downgrade."
    fi

    # Major version jump warning
    if (( tgt_major > cur_major )); then
        print_warning "Major version jump: ${cur_major} → ${tgt_major}"
        print_warning "Breaking changes are expected. Review the changelog before proceeding."
        if [[ "${FORCE}" -eq 0 ]]; then
            confirm_or_exit "Continue with major upgrade?" "Upgrade cancelled."
        fi
    fi

    print_success "Version compatibility OK"
}

# =============================================================================
# STOP SERVICES
# =============================================================================

stop_services() {
    print_step "Stopping services"

    docker compose down
    print_success "Services stopped"

    # Verify no containers remain for this project
    local running
    running=$(docker compose ps --services --filter "status=running" 2>/dev/null | wc -l || echo "0")
    if [[ "${running}" -gt 0 ]]; then
        print_warning "Some containers may still be running. Proceeding."
    fi
}

# =============================================================================
# CAPTURE DEPENDENCY HASHES  (call BEFORE pull)
# =============================================================================

capture_dependency_hashes() {
    _get_hash_cmd
    if [[ -z "${HASH_CMD}" ]]; then
        # No hash tool — treat everything as changed
        HASH_REQUIREMENTS="none"
        HASH_PACKAGE_JSON="none"
        HASH_DOCKERFILE="none"
        return 0
    fi

    HASH_REQUIREMENTS=""
    HASH_PACKAGE_JSON=""
    HASH_DOCKERFILE=""

    [[ -f "${ROOT_DIR}/requirements.txt" ]] && \
        HASH_REQUIREMENTS=$(${HASH_CMD} "${ROOT_DIR}/requirements.txt" | awk '{print $1}')

    [[ -f "${ROOT_DIR}/src/web_terminal_client/package.json" ]] && \
        HASH_PACKAGE_JSON=$(${HASH_CMD} "${ROOT_DIR}/src/web_terminal_client/package.json" | awk '{print $1}')

    [[ -f "${ROOT_DIR}/Dockerfile" ]] && \
        HASH_DOCKERFILE=$(${HASH_CMD} "${ROOT_DIR}/Dockerfile" | awk '{print $1}')
}

# =============================================================================
# PULL CODE
# =============================================================================

pull_code() {
    print_step "Pulling latest code"

    # Warn about local uncommitted changes
    if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null; then
        print_warning "You have local uncommitted changes."
        print_warning "These will remain after the pull but may cause conflicts."
    fi

    git fetch origin main
    print_success "Fetched origin/main"

    git pull --rebase origin main
    print_success "Code updated via rebase"
}

# =============================================================================
# DETECT DEPENDENCY CHANGES  (call AFTER pull, uses pre-pull hashes)
# =============================================================================

detect_dependency_changes() {
    print_step "Detecting dependency changes"

    if [[ -z "${HASH_REQUIREMENTS}" && -z "${HASH_PACKAGE_JSON}" && -z "${HASH_DOCKERFILE}" ]]; then
        print_warning "No pre-pull hashes available. Assuming no-cache build is not needed."
        return 0
    fi

    _get_hash_cmd
    if [[ -z "${HASH_CMD}" ]]; then
        NEEDS_NO_CACHE=1
        print_warning "No hash tool found — will use --no-cache build."
        return 0
    fi

    local changed=()

    if [[ -f "${ROOT_DIR}/requirements.txt" ]]; then
        local new_hash
        new_hash=$(${HASH_CMD} "${ROOT_DIR}/requirements.txt" | awk '{print $1}')
        if [[ "${new_hash}" != "${HASH_REQUIREMENTS}" ]]; then
            changed+=("requirements.txt")
        fi
    fi

    if [[ -f "${ROOT_DIR}/src/web_terminal_client/package.json" ]]; then
        local new_hash
        new_hash=$(${HASH_CMD} "${ROOT_DIR}/src/web_terminal_client/package.json" | awk '{print $1}')
        if [[ "${new_hash}" != "${HASH_PACKAGE_JSON}" ]]; then
            changed+=("package.json")
        fi
    fi

    if [[ -f "${ROOT_DIR}/Dockerfile" ]]; then
        local new_hash
        new_hash=$(${HASH_CMD} "${ROOT_DIR}/Dockerfile" | awk '{print $1}')
        if [[ "${new_hash}" != "${HASH_DOCKERFILE}" ]]; then
            changed+=("Dockerfile")
        fi
    fi

    if [[ ${#changed[@]} -gt 0 ]]; then
        NEEDS_NO_CACHE=1
        print_warning "Dependency files changed: ${changed[*]}"
        print_info "Will use --no-cache build to ensure clean image."
    else
        print_success "No dependency changes detected — using cached build"
    fi
}

# =============================================================================
# RUN BUILD
# =============================================================================

run_build() {
    print_step "Building containers"

    if [[ "${NEEDS_NO_CACHE}" -eq 1 ]]; then
        print_info "Dependency changes detected — using --no-cache build"
        "${ROOT_DIR}/run.sh" build --no-cache
    else
        "${ROOT_DIR}/run.sh" build
    fi

    print_success "Build complete"
}

# =============================================================================
# RUN CONFIG MIGRATION
# =============================================================================

run_config_migration() {
    local migration_script="${ROOT_DIR}/scripts/migrate_config.py"

    if [[ ! -f "${migration_script}" ]]; then
        print_dim "  scripts/migrate_config.py not found — skipping config migration"
        return 0
    fi

    print_step "Running config migration"

    # Per-file backups handled by Python's migrate_file() before writing.
    # Full config/ is already archived in create_backup() for rollback safety.

    local dry_run_flag=""
    [[ "${DRY_RUN}" -eq 1 ]] && dry_run_flag="--dry-run"

    python3 "${migration_script}" \
        --from "${CURRENT_VERSION}" \
        --to   "${TARGET_VERSION}" \
        ${dry_run_flag}

    print_success "Config migration complete"
}

# =============================================================================
# VALIDATE UPGRADE
# =============================================================================

validate_upgrade() {
    print_step "Validating upgrade"

    local errors=0

    # Health endpoint
    print_info "Checking API health endpoint..."
    local retries=10
    local ok=0
    for (( i=1; i<=retries; i++ )); do
        if curl -sf "http://localhost:${API_PORT}/api/v1/health" > /dev/null 2>&1; then
            ok=1
            break
        fi
        print_dim "  Attempt ${i}/${retries} — waiting for API..."
        sleep 3
    done

    if [[ "${ok}" -eq 1 ]]; then
        print_success "API health check passed"
    else
        print_error "API health check failed after ${retries} attempts"
        (( errors++ ))
    fi

    # Database check
    local db_file="${ROOT_DIR}/data/ag3ntum.db"
    if [[ -f "${db_file}" ]]; then
        local db_size
        db_size=$(stat -c%s "${db_file}" 2>/dev/null || stat -f%z "${db_file}" 2>/dev/null || echo "0")
        if [[ "${db_size}" -gt 0 ]]; then
            print_success "Database exists and is non-empty (${db_size} bytes)"
        else
            print_warning "Database exists but is empty"
        fi
    else
        print_warning "Database file not found at data/ag3ntum.db"
    fi

    # Version match
    local installed_version
    if [[ -f "${ROOT_DIR}/.ag3ntum-version" ]]; then
        installed_version=$(tr -d '[:space:]' < "${ROOT_DIR}/.ag3ntum-version")
    else
        installed_version=""
    fi

    if [[ "${installed_version}" == "${TARGET_VERSION}" ]]; then
        print_success ".ag3ntum-version matches target (${TARGET_VERSION})"
    else
        print_warning ".ag3ntum-version (${installed_version}) does not match VERSION (${TARGET_VERSION})"
        print_info "Updating .ag3ntum-version to ${TARGET_VERSION}"
        printf '%s\n' "${TARGET_VERSION}" > "${ROOT_DIR}/.ag3ntum-version"
    fi

    if [[ "${errors}" -gt 0 ]]; then
        print_error "Validation failed with ${errors} error(s). Review output above."
        exit 1
    fi

    print_success "All validation checks passed"
}

# =============================================================================
# ROLLBACK
# =============================================================================

do_rollback() {
    print_step "Rolling back to previous version"

    if [[ ! -d "${BACKUPS_DIR}" ]]; then
        print_error "No backups directory found at ${BACKUPS_DIR}."
        exit 1
    fi

    # Find most recent backup
    local latest_backup
    latest_backup=$(
        find "${BACKUPS_DIR}" -maxdepth 1 -name "upgrade-*.tar.gz" \
            -printf '%T@ %p\n' 2>/dev/null \
            | sort -rn \
            | head -1 \
            | awk '{print $2}'
    )

    if [[ -z "${latest_backup}" ]]; then
        print_error "No backup archives found in ${BACKUPS_DIR}."
        exit 1
    fi

    print_info "Restoring from: $(basename "${latest_backup}")"

    if [[ "${FORCE}" -eq 0 ]]; then
        confirm_or_exit \
            "This will overwrite data/ and config/ with the backup. Continue?" \
            "Rollback cancelled."
    fi

    # Stop running services
    if docker compose ps --services --filter "status=running" 2>/dev/null | grep -q .; then
        print_info "Stopping services before rollback..."
        docker compose down
    fi

    # Extract to temporary location
    local tmp_dir
    tmp_dir=$(mktemp -d "${BACKUPS_DIR}/rollback-XXXXXX")

    tar -xzf "${latest_backup}" -C "${tmp_dir}"

    # Restore data/
    if [[ -d "${tmp_dir}/data" ]]; then
        print_info "Restoring data/..."
        rm -rf "${ROOT_DIR}/data"
        cp -r "${tmp_dir}/data" "${ROOT_DIR}/data"
        print_success "data/ restored"
    fi

    # Restore config/
    if [[ -d "${tmp_dir}/config" ]]; then
        print_info "Restoring config/..."
        # Merge: do not remove config files that were NOT in the backup
        cp -r "${tmp_dir}/config/." "${ROOT_DIR}/config/"
        print_success "config/ restored"
    fi

    # Restore .env
    if [[ -f "${tmp_dir}/.env" ]]; then
        cp "${tmp_dir}/.env" "${ROOT_DIR}/.env"
        print_success ".env restored"
    fi

    # Restore .ag3ntum-version and read rollback target version
    local rollback_version=""
    if [[ -f "${tmp_dir}/.ag3ntum-version" ]]; then
        cp "${tmp_dir}/.ag3ntum-version" "${ROOT_DIR}/.ag3ntum-version"
        rollback_version=$(tr -d '[:space:]' < "${tmp_dir}/.ag3ntum-version")
        print_success ".ag3ntum-version restored (${rollback_version})"
    fi

    rm -rf "${tmp_dir}"

    # Try to checkout the version tag in git
    if [[ -n "${rollback_version}" ]]; then
        if git rev-parse "v${rollback_version}" > /dev/null 2>&1; then
            print_info "Checking out git tag v${rollback_version}..."
            git checkout "v${rollback_version}"
        else
            print_warning "Git tag v${rollback_version} not found. Staying on current branch."
        fi
    fi

    # Rebuild
    print_step "Rebuilding at rolled-back version"
    "${ROOT_DIR}/run.sh" build

    print_success "Rollback complete"
    if [[ -n "${rollback_version}" ]]; then
        print_info "Running at version: ${rollback_version}"
    fi
}

# =============================================================================
# HEALTH CHECK  (--check mode)
# =============================================================================

run_health_check() {
    printf "\n${BOLD}${CYAN}=== Ag3ntum Health Check ===${NC}\n\n"

    # Versions
    local installed_version
    installed_version="${CURRENT_VERSION:-$(get_current_version)}"
    local codebase_version="(unknown)"
    [[ -f "${ROOT_DIR}/VERSION" ]] && codebase_version=$(tr -d '[:space:]' < "${ROOT_DIR}/VERSION")

    printf "${BOLD}Versions${NC}\n"
    printf "  Installed (.ag3ntum-version): ${WHITE}%s${NC}\n" "${installed_version}"
    printf "  Codebase (VERSION):           ${WHITE}%s${NC}\n" "${codebase_version}"
    if [[ "${installed_version}" != "${codebase_version}" ]]; then
        printf "  ${YELLOW}Version mismatch — upgrade may be needed${NC}\n"
    fi

    # Docker service status
    printf "\n${BOLD}Docker Services${NC}\n"
    if docker compose ps 2>/dev/null; then
        : # output from docker compose ps
    else
        printf "  ${RED}docker compose ps failed${NC}\n"
    fi

    # Database
    printf "\n${BOLD}Database${NC}\n"
    local db_file="${ROOT_DIR}/data/ag3ntum.db"
    if [[ -f "${db_file}" ]]; then
        local db_size
        db_size=$(stat -c%s "${db_file}" 2>/dev/null || stat -f%z "${db_file}" 2>/dev/null || echo "0")
        printf "  File:     %s\n" "${db_file}"
        printf "  Size:     %s bytes\n" "${db_size}"
        if [[ -w "${db_file}" ]]; then
            printf "  Writable: ${GREEN}yes${NC}\n"
        else
            printf "  Writable: ${RED}no${NC}\n"
        fi
    else
        printf "  ${YELLOW}data/ag3ntum.db not found${NC}\n"
    fi

    # API health
    printf "\n${BOLD}API Health${NC}\n"
    if curl -sf "http://localhost:${API_PORT}/api/v1/health" > /dev/null 2>&1; then
        printf "  ${GREEN}OK${NC} http://localhost:${API_PORT}/api/v1/health\n"
    else
        printf "  ${RED}FAIL${NC} http://localhost:${API_PORT}/api/v1/health (not reachable)\n"
    fi

    # Disk space
    printf "\n${BOLD}Disk Space${NC}\n"
    df -h "${ROOT_DIR}" | awk 'NR==1 || NR==2'

    # Recent backups
    printf "\n${BOLD}Recent Backups${NC}\n"
    if [[ -d "${BACKUPS_DIR}" ]]; then
        local backup_count
        backup_count=$(find "${BACKUPS_DIR}" -maxdepth 1 -name "upgrade-*.tar.gz" | wc -l)
        if [[ "${backup_count}" -gt 0 ]]; then
            find "${BACKUPS_DIR}" -maxdepth 1 -name "upgrade-*.tar.gz" \
                -printf '  %TY-%Tm-%Td %TH:%TM  %f  (%s bytes)\n' 2>/dev/null \
                | sort -r
        else
            printf "  ${DIM}No backups found in %s${NC}\n" "${BACKUPS_DIR}"
        fi
    else
        printf "  ${DIM}Backup directory does not exist yet${NC}\n"
    fi

    printf "\n"
}

# =============================================================================
# CLEANUP IMAGES
# =============================================================================

cleanup_images() {
    print_step "Cleaning up old Docker images"

    # List ag3ntum images, keep the 2 most recent (current + previous)
    local images
    images=$(docker images --format '{{.ID}} {{.Repository}}:{{.Tag}} {{.CreatedAt}}' \
        | grep -E '^[a-f0-9]+ ag3ntum:' \
        | sort -k3 -r \
        | awk 'NR>2 {print $1}' \
        || true)

    if [[ -n "${images}" ]]; then
        printf '%s\n' "${images}" | xargs -r docker rmi --force 2>/dev/null || true
        print_success "Old ag3ntum images removed"
    else
        print_success "No old images to remove"
    fi

    # Also prune dangling images
    docker image prune -f > /dev/null 2>&1 || true
}

# =============================================================================
# INTERRUPT HANDLER
# =============================================================================

handle_interrupt() {
    printf "\n"
    print_warning "Upgrade interrupted by user (Ctrl+C)."

    case "${STAGE}" in
        pulling)
            print_info "Code pull may be incomplete. Run: git status"
            print_info "To retry: ./upgrade.sh"
            ;;
        stopping)
            print_info "Services are stopped. Run: ./run.sh build  to restart."
            ;;
        building)
            print_info "Build interrupted. Run: ./run.sh build  to retry."
            ;;
        migrating_config)
            print_info "Config migration interrupted. Check config/*.bak files."
            print_info "To retry migration manually: python3 scripts/migrate_config.py --from ${CURRENT_VERSION} --to ${TARGET_VERSION}"
            ;;
        validating)
            print_info "Build likely completed. Run: ./upgrade.sh --check  to verify state."
            ;;
        *)
            print_info "Run: ./upgrade.sh --check  to assess current state."
            ;;
    esac

    exit 130
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    parse_args "$@"

    # --- Check-only mode ---
    if [[ "${MODE}" == "check" ]]; then
        CURRENT_VERSION=$(get_current_version)
        run_health_check
        exit 0
    fi

    # --- Rollback mode ---
    if [[ "${MODE}" == "rollback" ]]; then
        do_rollback
        exit $?
    fi

    # --- Normal upgrade ---
    printf "\n${BOLD}${CYAN}=== Ag3ntum Upgrade ===${NC}\n\n"

    # Capture dependency hashes BEFORE pull so we can compare after
    capture_dependency_hashes

    CURRENT_VERSION=$(get_current_version)

    check_prerequisites

    print_info "Current version: ${CURRENT_VERSION}"

    if [[ "${DRY_RUN}" -eq 1 ]]; then
        printf "\n${YELLOW}[DRY RUN]${NC} No changes will be made.\n"
        printf "${YELLOW}[DRY RUN]${NC} Would perform: git pull, build, config migration\n\n"

        pull_code
        TARGET_VERSION=$(tr -d '[:space:]' < "${ROOT_DIR}/VERSION")
        print_info "Target version: ${TARGET_VERSION}"

        check_version_compatibility
        detect_dependency_changes

        run_config_migration   # passes --dry-run via DRY_RUN flag in the function

        printf "\n${YELLOW}[DRY RUN]${NC} Complete. No changes were made.\n"
        exit 0
    fi

    # Interactive confirmation
    if [[ "${FORCE}" -eq 0 ]]; then
        confirm_or_exit "This will pull the latest code and rebuild. Continue?" \
            "Upgrade cancelled."
    fi

    check_active_sessions
    create_backup

    # Interrupt handler (stage-aware recovery messages)
    trap 'handle_interrupt' INT

    STAGE="pulling"
    pull_code
    TARGET_VERSION=$(tr -d '[:space:]' < "${ROOT_DIR}/VERSION")
    print_info "Target version: ${TARGET_VERSION}"

    check_version_compatibility
    detect_dependency_changes

    STAGE="stopping"
    stop_services

    STAGE="migrating_config"
    run_config_migration

    STAGE="building"
    run_build

    STAGE="validating"
    validate_upgrade

    cleanup_images

    # Update installed-version marker
    printf '%s\n' "${TARGET_VERSION}" > "${ROOT_DIR}/.ag3ntum-version"

    printf "\n${BOLD}${GREEN}=== Upgrade Complete ===${NC}\n"
    printf "  ${DIM}%s${NC} ${GREEN}→${NC} ${BOLD}%s${NC}\n" "${CURRENT_VERSION}" "${TARGET_VERSION}"
    printf "\n"
}

main "$@"
