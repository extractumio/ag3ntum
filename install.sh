#!/usr/bin/env bash
#
# Ag3ntum One-Command Installer
# https://github.com/extractumio/ag3ntum
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/extractumio/ag3ntum/main/install.sh | bash
#
# Or download and run:
#   chmod +x install.sh && ./install.sh
#
# This script:
#   1. Checks prerequisites (Docker, Git)
#   2. Clones the repository (if needed)
#   3. Prompts for configuration (API key, admin credentials)
#   4. Generates configuration files
#   5. Builds and starts containers
#   6. Creates admin user
#

set -euo pipefail

# =============================================================================
# CONSTANTS
# =============================================================================

VERSION="1.0.0"
REPO_URL="https://github.com/extractumio/ag3ntum.git"
MIN_DOCKER_VERSION="20.10"

# Braille spinner frames (same as web terminal)
SPINNER_FRAMES=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')

# Colors (ANSI escape codes)
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
WHITE='\033[0;37m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m' # No Color

# Configuration defaults
DEFAULT_API_PORT="40080"
DEFAULT_WEB_PORT="50080"
DEFAULT_HOSTNAME="localhost"

# Global state
DETECTED_OS=""
IN_REPO=0

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

print_info() {
    printf "${CYAN}ℹ${NC} %s\n" "$1"
}

print_success() {
    printf "${GREEN}✓${NC} %s\n" "$1"
}

print_warning() {
    printf "${YELLOW}⚠${NC} %s\n" "$1"
}

print_error() {
    printf "${RED}✗${NC} %s\n" "$1" >&2
}

print_step() {
    printf "\n${BOLD}${BLUE}▶${NC} ${BOLD}%s${NC}\n" "$1"
}

print_dim() {
    printf "${DIM}%s${NC}\n" "$1"
}

# Animated spinner for long-running operations
# Usage: long_command & spinner $! "Message"
spinner() {
    local pid=$1
    local message=$2
    local i=0
    local frame_count=${#SPINNER_FRAMES[@]}

    # Hide cursor
    printf "\033[?25l"

    while kill -0 "$pid" 2>/dev/null; do
        printf "\r${CYAN}%s${NC} %s" "${SPINNER_FRAMES[$i]}" "$message"
        i=$(( (i + 1) % frame_count ))
        sleep 0.1
    done

    # Show cursor and clear line
    printf "\033[?25h"
    printf "\r\033[K"

    # Check exit status
    wait "$pid"
    return $?
}

# Prompt for text input with default value
prompt_text() {
    local prompt=$1
    local default=$2
    local var_name=$3
    local result

    if [[ -n "$default" ]]; then
        printf "${CYAN}?${NC} %s [${WHITE}%s${NC}]: " "$prompt" "$default"
    else
        printf "${CYAN}?${NC} %s: " "$prompt"
    fi

    read -r result
    result="${result:-$default}"
    eval "$var_name='$result'"
}

# Prompt for password (no echo, with confirmation)
prompt_password() {
    local prompt=$1
    local var_name=$2
    local result
    local confirm

    while true; do
        printf "${CYAN}?${NC} %s: " "$prompt"
        read -rs result
        printf "\n"

        if [[ ${#result} -lt 8 ]]; then
            print_warning "Password must be at least 8 characters"
            continue
        fi

        printf "${CYAN}?${NC} Confirm password: "
        read -rs confirm
        printf "\n"

        if [[ "$result" != "$confirm" ]]; then
            print_warning "Passwords do not match"
            continue
        fi

        break
    done

    eval "$var_name='$result'"
}

# Prompt for yes/no answer
prompt_yesno() {
    local prompt=$1
    local default=${2:-y}
    local result

    local hint="[Y/n]"
    [[ "$default" == "n" ]] && hint="[y/N]"

    printf "${CYAN}?${NC} %s %s: " "$prompt" "$hint"
    read -r result
    result="${result:-$default}"

    [[ "$result" =~ ^[Yy] ]]
}

# Validate API key format
validate_api_key() {
    local key=$1
    if [[ ! "$key" =~ ^sk-ant- ]]; then
        return 1
    fi
    if [[ ${#key} -lt 20 ]]; then
        return 1
    fi
    return 0
}

# Validate port number
validate_port() {
    local port=$1
    if [[ ! "$port" =~ ^[0-9]+$ ]]; then
        return 1
    fi
    if [[ "$port" -lt 1024 ]] || [[ "$port" -gt 65535 ]]; then
        return 1
    fi
    return 0
}

# Check if a port is available
check_port_available() {
    local port=$1

    if command -v lsof &>/dev/null; then
        ! lsof -i ":$port" &>/dev/null
    elif command -v ss &>/dev/null; then
        ! ss -tuln 2>/dev/null | grep -q ":$port "
    elif command -v netstat &>/dev/null; then
        ! netstat -tuln 2>/dev/null | grep -q ":$port "
    else
        # Cannot check, assume available
        return 0
    fi
}

# =============================================================================
# PREREQUISITE CHECKS
# =============================================================================

detect_os() {
    local os=""
    local version=""

    case "$(uname -s)" in
        Darwin)
            os="macos"
            version=$(sw_vers -productVersion 2>/dev/null || echo "unknown")
            ;;
        Linux)
            if [[ -f /etc/os-release ]]; then
                # shellcheck source=/dev/null
                . /etc/os-release
                os="${ID:-linux}"
                version="${VERSION_ID:-unknown}"
            else
                os="linux"
                version="unknown"
            fi
            ;;
        MINGW*|MSYS*|CYGWIN*)
            os="windows"
            version="git-bash"
            ;;
        *)
            os="unknown"
            version="unknown"
            ;;
    esac

    DETECTED_OS="$os:$version"
}

check_docker() {
    if ! command -v docker &>/dev/null; then
        print_error "Docker is not installed"
        echo ""
        case "$DETECTED_OS" in
            macos*)
                echo "Install Docker Desktop for Mac:"
                echo "  ${CYAN}https://docs.docker.com/desktop/install/mac-install/${NC}"
                ;;
            ubuntu*|debian*)
                echo "Install Docker on Ubuntu/Debian:"
                echo "  ${CYAN}curl -fsSL https://get.docker.com | sh${NC}"
                echo "  ${CYAN}sudo usermod -aG docker \$USER${NC}"
                echo ""
                echo "Then log out and back in (or reboot) for group changes to take effect."
                ;;
            *)
                echo "Install Docker: ${CYAN}https://docs.docker.com/get-docker/${NC}"
                ;;
        esac
        return 1
    fi

    # Check Docker daemon is running
    if ! docker info &>/dev/null; then
        print_error "Docker daemon is not running"
        echo ""
        case "$DETECTED_OS" in
            macos*)
                echo "Start Docker Desktop and try again."
                ;;
            *)
                echo "Start the Docker service:"
                echo "  ${CYAN}sudo systemctl start docker${NC}"
                ;;
        esac
        return 1
    fi

    # Get and check version
    local docker_version
    docker_version=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "0.0")

    # Version comparison
    local major minor
    IFS='.' read -r major minor _ <<< "$docker_version"
    local min_major min_minor
    IFS='.' read -r min_major min_minor <<< "$MIN_DOCKER_VERSION"

    if [[ "$major" -lt "$min_major" ]] || { [[ "$major" -eq "$min_major" ]] && [[ "$minor" -lt "$min_minor" ]]; }; then
        print_warning "Docker version $docker_version is older than recommended $MIN_DOCKER_VERSION"
    fi

    print_success "Docker $docker_version"
    return 0
}

check_docker_compose() {
    local compose_cmd=""
    local compose_version=""

    # Check for docker compose (v2) first
    if docker compose version &>/dev/null 2>&1; then
        compose_cmd="docker compose"
        compose_version=$(docker compose version --short 2>/dev/null || echo "unknown")
    elif command -v docker-compose &>/dev/null; then
        compose_cmd="docker-compose"
        compose_version=$(docker-compose version --short 2>/dev/null || echo "unknown")
    else
        print_error "Docker Compose is not installed"
        echo ""
        echo "Docker Compose v2 is included with Docker Desktop."
        echo "For standalone installation:"
        echo "  ${CYAN}https://docs.docker.com/compose/install/${NC}"
        return 1
    fi

    print_success "Docker Compose $compose_version ($compose_cmd)"
    return 0
}

check_git() {
    if ! command -v git &>/dev/null; then
        print_error "Git is not installed"
        echo ""
        case "$DETECTED_OS" in
            macos*)
                echo "Install Git with Xcode Command Line Tools:"
                echo "  ${CYAN}xcode-select --install${NC}"
                ;;
            ubuntu*|debian*)
                echo "Install Git:"
                echo "  ${CYAN}sudo apt-get update && sudo apt-get install -y git${NC}"
                ;;
            *)
                echo "Install Git: ${CYAN}https://git-scm.com/downloads${NC}"
                ;;
        esac
        return 1
    fi

    local git_version
    git_version=$(git --version | awk '{print $3}')
    print_success "Git $git_version"
    return 0
}

check_curl() {
    if ! command -v curl &>/dev/null; then
        print_error "curl is not installed"
        return 1
    fi
    print_success "curl available"
    return 0
}

run_prerequisite_checks() {
    print_step "Checking Prerequisites"

    local failed=0

    check_docker || failed=1
    check_docker_compose || failed=1
    check_git || failed=1
    check_curl || failed=1

    if [[ $failed -eq 1 ]]; then
        echo ""
        print_error "Please install missing prerequisites and run the installer again."
        exit 1
    fi

    print_success "All prerequisites satisfied"
}

# =============================================================================
# REPOSITORY SETUP
# =============================================================================

setup_repository() {
    print_step "Setting Up Repository"

    # Check if we're already in the ag3ntum directory (run.sh exists)
    if [[ -f "run.sh" ]] && [[ -f "docker-compose.yml" ]] && [[ -d "config" ]]; then
        print_success "Already in Ag3ntum directory"
        IN_REPO=1
        return 0
    fi

    # Check if ag3ntum directory exists in current location
    if [[ -d "ag3ntum" ]]; then
        print_info "Found existing ag3ntum directory"
        cd ag3ntum || exit 1

        if [[ -f "run.sh" ]] && [[ -f "docker-compose.yml" ]]; then
            print_success "Using existing repository"
            IN_REPO=1
            return 0
        fi
    fi

    # Clone the repository
    print_info "Cloning Ag3ntum repository..."

    # Clone with progress indicator
    local clone_output
    clone_output=$(mktemp)

    (git clone --depth 1 "$REPO_URL" ag3ntum > "$clone_output" 2>&1) &
    local clone_pid=$!

    if ! spinner $clone_pid "Cloning repository"; then
        print_error "Failed to clone repository"
        cat "$clone_output"
        rm -f "$clone_output"
        exit 1
    fi
    rm -f "$clone_output"

    cd ag3ntum || exit 1
    IN_REPO=1
    print_success "Repository cloned successfully"
}

# =============================================================================
# CONFIGURATION GATHERING
# =============================================================================

gather_configuration() {
    print_step "Configuration"

    echo ""
    print_info "Please provide the following configuration values."
    print_info "Press Enter to accept defaults shown in [brackets]."
    echo ""

    # API Key (required, sensitive)
    while true; do
        printf "${CYAN}?${NC} Anthropic API Key: "
        read -rs ANTHROPIC_API_KEY
        printf "\n"

        if [[ -z "$ANTHROPIC_API_KEY" ]]; then
            print_warning "API key is required"
            print_dim "  Get yours at: https://console.anthropic.com/settings/keys"
            continue
        fi

        if ! validate_api_key "$ANTHROPIC_API_KEY"; then
            print_warning "Invalid API key format (should start with 'sk-ant-')"
            continue
        fi

        break
    done
    print_success "API key configured"

    echo ""

    # Admin credentials
    print_info "Create admin account"

    prompt_text "Admin username" "admin" ADMIN_USERNAME

    # Validate username
    while [[ ! "$ADMIN_USERNAME" =~ ^[a-zA-Z0-9_]{3,32}$ ]]; do
        print_warning "Username must be 3-32 alphanumeric characters or underscore"
        prompt_text "Admin username" "admin" ADMIN_USERNAME
    done

    while true; do
        prompt_text "Admin email" "" ADMIN_EMAIL
        if [[ "$ADMIN_EMAIL" == *"@"*"."* ]]; then
            break
        fi
        print_warning "Please enter a valid email address"
    done

    prompt_password "Admin password" ADMIN_PASSWORD

    print_success "Admin credentials configured"

    echo ""

    # Server configuration
    print_info "Server configuration"

    prompt_text "Public hostname or IP" "$DEFAULT_HOSTNAME" SERVER_HOSTNAME

    # API port
    while true; do
        prompt_text "API port" "$DEFAULT_API_PORT" API_PORT
        if validate_port "$API_PORT"; then
            if ! check_port_available "$API_PORT"; then
                print_warning "Port $API_PORT appears to be in use"
                if ! prompt_yesno "Continue anyway?" "n"; then
                    continue
                fi
            fi
            break
        fi
        print_warning "Invalid port number (must be 1024-65535)"
    done

    # Web UI port
    while true; do
        prompt_text "Web UI port" "$DEFAULT_WEB_PORT" WEB_PORT
        if validate_port "$WEB_PORT"; then
            if [[ "$WEB_PORT" == "$API_PORT" ]]; then
                print_warning "Web port must be different from API port"
                continue
            fi
            if ! check_port_available "$WEB_PORT"; then
                print_warning "Port $WEB_PORT appears to be in use"
                if ! prompt_yesno "Continue anyway?" "n"; then
                    continue
                fi
            fi
            break
        fi
        print_warning "Invalid port number (must be 1024-65535)"
    done

    print_success "Server configuration complete"
}

# =============================================================================
# CONFIGURATION FILE GENERATION
# =============================================================================

generate_secrets_yaml() {
    print_info "Generating secrets.yaml..."

    cat > config/secrets.yaml << EOF
# Ag3ntum Secrets Configuration
# Generated by install.sh on $(date -Iseconds 2>/dev/null || date)
# IMPORTANT: Keep this file secure and never commit to version control

# Anthropic API Key
anthropic_api_key: ${ANTHROPIC_API_KEY}

# Fernet encryption key (auto-generated on first run)
# Used to encrypt user API keys in the Token table.
# fernet_key: <auto-generated>
EOF

    # Secure the file immediately
    chmod 600 config/secrets.yaml

    # Clear the variable from memory
    unset ANTHROPIC_API_KEY

    print_success "secrets.yaml created (permissions: 600)"
}

generate_api_yaml() {
    print_info "Generating api.yaml..."

    # Copy from example if it exists
    if [[ -f "config/api.yaml.example" ]]; then
        cp config/api.yaml.example config/api.yaml
    else
        print_warning "api.yaml.example not found, creating minimal config"
        cat > config/api.yaml << EOF
server:
  hostname: "${SERVER_HOSTNAME}"
  protocol: "http"
  trusted_proxies: []

api:
  host: "0.0.0.0"
  port: 40080
  external_port: ${API_PORT}
  reload: false

web:
  host: "0.0.0.0"
  port: 50080
  external_port: ${WEB_PORT}

security:
  enable_security_headers: true
  validate_host_header: true
  content_security_policy: "strict"
  additional_allowed_hosts: []

database:
  path: "./data/ag3ntum.db"

jwt:
  algorithm: "HS256"
  expiry_hours: 168

redis:
  url: "redis://redis:6379/0"
  max_connections: 50
  socket_timeout: 5.0
  socket_connect_timeout: 5.0
  decode_responses: false

task_queue:
  auto_resume:
    enabled: true
    max_session_age_hours: 6
    max_resume_attempts: 3
    resume_delay_seconds: 5
  queue:
    enabled: true
    processing_interval_ms: 500
    max_queue_size: 1000
    task_timeout_minutes: 30
  quotas:
    global_max_concurrent: 4
    per_user_max_concurrent: 2
    per_user_daily_limit: 50
EOF
        print_success "api.yaml created"
        return
    fi

    # Update values - use sed with different delimiters to handle special chars
    # macOS and GNU sed have different -i syntax
    local sed_inplace
    if [[ "$(uname)" == "Darwin" ]]; then
        sed_inplace="sed -i ''"
    else
        sed_inplace="sed -i"
    fi

    # Update hostname
    $sed_inplace "s|hostname: \"localhost\"|hostname: \"${SERVER_HOSTNAME}\"|" config/api.yaml 2>/dev/null || true

    # Update API external port (appears in api section)
    $sed_inplace "s|external_port: 40080|external_port: ${API_PORT}|" config/api.yaml 2>/dev/null || true

    # Update Web external port (appears in web section) - need to be careful not to change api port
    # This is a bit tricky since both are "external_port" - we'll use a two-step approach
    if [[ "$WEB_PORT" != "$DEFAULT_WEB_PORT" ]]; then
        # Create temp file to track section
        awk -v api_port="$API_PORT" -v web_port="$WEB_PORT" '
        /^api:/ { in_api=1; in_web=0 }
        /^web:/ { in_api=0; in_web=1 }
        /^[a-z]/ && !/^api:/ && !/^web:/ { in_api=0; in_web=0 }
        /external_port:/ {
            if (in_web) {
                sub(/external_port: [0-9]+/, "external_port: " web_port)
            }
        }
        { print }
        ' config/api.yaml > config/api.yaml.tmp && mv config/api.yaml.tmp config/api.yaml
    fi

    # Clean up any backup files created by sed
    rm -f config/api.yaml.bak config/api.yaml''

    print_success "api.yaml created"
}

generate_agent_yaml() {
    print_info "Generating agent.yaml..."

    if [[ -f "config/agent.yaml.example" ]]; then
        cp config/agent.yaml.example config/agent.yaml
        print_success "agent.yaml created (using defaults)"
    else
        print_warning "agent.yaml.example not found, skipping"
    fi
}

generate_configuration() {
    print_step "Generating Configuration Files"

    # Ensure config directory exists
    mkdir -p config

    generate_secrets_yaml
    generate_api_yaml
    generate_agent_yaml

    print_success "Configuration files generated"
}

# =============================================================================
# BUILD AND DEPLOY
# =============================================================================

run_build() {
    print_step "Building Ag3ntum"

    print_info "This may take 5-10 minutes on first build..."
    echo ""

    # Export port variables for run.sh to read from config
    export AG3NTUM_API_PORT="$API_PORT"
    export AG3NTUM_WEB_PORT="$WEB_PORT"

    # Run the build with output
    if ! ./run.sh rebuild --no-cache; then
        print_error "Build failed"
        echo ""
        echo "Check the output above for errors."
        echo "You can also check Docker logs:"
        echo "  ${CYAN}docker compose logs${NC}"
        exit 1
    fi

    print_success "Build completed successfully"
}

create_admin_user() {
    print_step "Creating Admin User"

    print_info "Creating user: $ADMIN_USERNAME ($ADMIN_EMAIL)"

    if ! ./run.sh create-user \
        --username="$ADMIN_USERNAME" \
        --email="$ADMIN_EMAIL" \
        --password="$ADMIN_PASSWORD" \
        --admin; then
        print_warning "Failed to create admin user automatically"
        echo ""
        echo "You can create it manually:"
        echo "  ${CYAN}./run.sh create-user --username=$ADMIN_USERNAME --email=$ADMIN_EMAIL --password=YOUR_PASSWORD --admin${NC}"
    else
        print_success "Admin user created"
    fi

    # Clear password from memory
    unset ADMIN_PASSWORD
}

# =============================================================================
# COMPLETION
# =============================================================================

show_banner() {
    printf "${CYAN}"
    cat << 'EOF'

    _    ____ _____       _
   / \  / ___|___ / _ __ | |_ _   _ _ __ ___
  / _ \| |  _  |_ \| '_ \| __| | | | '_ ` _ \
 / ___ \ |_| |___) | | | | |_| |_| | | | | | |
/_/   \_\____|____/|_| |_|\__|\__,_|_| |_| |_|

EOF
    printf "${NC}"
    printf "${WHITE}Self-hosted Claude Code execution platform${NC}\n"
    printf "${DIM}Version ${VERSION}${NC}\n"
    echo ""
}

show_completion() {
    echo ""
    printf "${GREEN}"
    cat << 'EOF'
 ___           _        _ _       _   _               ____                      _      _       _
|_ _|_ __  ___| |_ __ _| | | __ _| |_(_) ___  _ __   / ___|___  _ __ ___  _ __ | | ___| |_ ___| |
 | || '_ \/ __| __/ _` | | |/ _` | __| |/ _ \| '_ \ | |   / _ \| '_ ` _ \| '_ \| |/ _ \ __/ _ \ |
 | || | | \__ \ || (_| | | | (_| | |_| | (_) | | | || |__| (_) | | | | | | |_) | |  __/ ||  __/_|
|___|_| |_|___/\__\__,_|_|_|\__,_|\__|_|\___/|_| |_(_)____\___/|_| |_| |_| .__/|_|\___|\__\___(_)
                                                                        |_|
EOF
    printf "${NC}"
    echo ""

    local protocol="http"
    local web_url="${protocol}://${SERVER_HOSTNAME}:${WEB_PORT}"
    local api_url="${protocol}://${SERVER_HOSTNAME}:${API_PORT}"

    printf "${BOLD}Web Interface:${NC}  ${CYAN}%s${NC}\n" "$web_url"
    printf "${BOLD}API Endpoint:${NC}   ${CYAN}%s/api/v1${NC}\n" "$api_url"
    printf "${BOLD}API Docs:${NC}       ${CYAN}%s/api/docs${NC}\n" "$api_url"
    echo ""
    printf "${BOLD}Login with:${NC}\n"
    printf "  Email:    ${WHITE}%s${NC}\n" "$ADMIN_EMAIL"
    printf "  Password: ${WHITE}(the password you entered)${NC}\n"
    echo ""
    printf "${YELLOW}Useful commands:${NC}\n"
    echo "  ./run.sh restart       # Restart after code changes"
    echo "  ./run.sh cleanup       # Stop and remove containers"
    echo "  ./run.sh create-user   # Create additional users"
    echo "  ./run.sh shell         # Shell into container"
    echo "  docker compose logs -f # View logs"
    echo ""

    if [[ "$SERVER_HOSTNAME" != "localhost" ]] && [[ "$SERVER_HOSTNAME" != "127.0.0.1" ]]; then
        printf "${YELLOW}Firewall note:${NC} Ensure ports ${WEB_PORT} (Web) and ${API_PORT} (API) are open.\n"
        echo ""
    fi

    print_success "Ag3ntum is ready to use!"
}

# =============================================================================
# CLEANUP HANDLER
# =============================================================================

cleanup_on_exit() {
    # Clear any sensitive variables that might still be set
    unset ANTHROPIC_API_KEY 2>/dev/null || true
    unset ADMIN_PASSWORD 2>/dev/null || true

    # Show cursor (in case hidden by spinner)
    printf "\033[?25h" 2>/dev/null || true
}

trap cleanup_on_exit EXIT INT TERM

# =============================================================================
# MAIN
# =============================================================================

main() {
    # Show welcome banner
    show_banner

    # Detect operating system
    detect_os

    print_dim "Detected OS: $DETECTED_OS"
    echo ""

    # Check prerequisites
    run_prerequisite_checks

    # Setup repository (clone if needed)
    setup_repository

    # Gather configuration from user
    gather_configuration

    # Generate configuration files
    generate_configuration

    # Build and deploy
    run_build

    # Create admin user
    create_admin_user

    # Show completion message
    show_completion
}

# Run main function with all script arguments
main "$@"
