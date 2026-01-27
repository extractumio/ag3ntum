# Ag3ntum Quick Start Guide

Deploy Ag3ntum on your server - three ways to get started.

## Table of Contents

- [Quick Install (Recommended)](#quick-install-recommended)
- [Manual Installation](#manual-installation)
- [Configuration Guide](#configuration-guide)
- [Troubleshooting](#troubleshooting)

---

## Quick Install (Recommended)

Deploy Ag3ntum with a single command:

```bash
curl -fsSL https://raw.githubusercontent.com/extractumio/ag3ntum/main/install.sh | bash
```

The installer will:
1. Check prerequisites (Docker, Git)
2. Clone the repository
3. Prompt for configuration (API key, admin credentials)
4. Build and start containers
5. Create your admin account

**Requirements:**
- Docker 20.10+ with Docker Compose v2
- Git
- 2GB RAM (4GB recommended)
- [Anthropic API key](https://console.anthropic.com/settings/keys)

**Supported Platforms:**
- macOS (Intel & Apple Silicon)
- Ubuntu 20.04+, Debian 11+
- Windows (via WSL2 or Git Bash)

### What the Installer Does

```
                    ┌──────────────────┐
                    │  Check Docker,   │
                    │  Git, Compose    │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │  Clone Ag3ntum   │
                    │   Repository     │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │  Interactive     │
                    │  Configuration   │
                    │  - API Key       │
                    │  - Admin creds   │
                    │  - Server host   │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │  Generate Config │
                    │  - secrets.yaml  │
                    │  - api.yaml      │
                    │  - agent.yaml    │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │  Docker Build    │
                    │  (5-10 minutes)  │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │  Create Admin    │
                    │     User         │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │    Ready!        │
                    │  http://...:50080│
                    └──────────────────┘
```

---

## Manual Installation

If you prefer step-by-step control, follow these instructions.

### Prerequisites

Install these before proceeding:

| Requirement | Minimum Version | Install Guide |
|-------------|-----------------|---------------|
| Docker | 20.10+ | [docs.docker.com](https://docs.docker.com/get-docker/) |
| Docker Compose | v2.0+ | Included with Docker Desktop |
| Git | Any recent | [git-scm.com](https://git-scm.com/downloads) |

**Check your versions:**
```bash
docker --version        # Should show 20.10 or higher
docker compose version  # Should show v2.x
git --version
```

### Step 1: Clone the Repository

```bash
git clone https://github.com/extractumio/ag3ntum.git
cd ag3ntum
```

### Step 2: Create Configuration Files

Copy the example configuration files:

```bash
# Required configuration files
cp config/secrets.yaml.example config/secrets.yaml
cp config/api.yaml.example config/api.yaml
cp config/agent.yaml.example config/agent.yaml
```

### Step 3: Configure Your API Key

Edit `config/secrets.yaml` with your Anthropic API key:

```yaml
# config/secrets.yaml
anthropic_api_key: sk-ant-api03-YOUR_KEY_HERE
```

Get your API key at [console.anthropic.com](https://console.anthropic.com/settings/keys).

**Important:** Set proper permissions on this file:
```bash
chmod 600 config/secrets.yaml
```

### Step 4: Configure Server Settings (Optional)

For remote access, edit `config/api.yaml`:

```yaml
# config/api.yaml
server:
  hostname: "your-server-ip-or-domain"  # Change from "localhost"
  protocol: "http"                       # Use "https" with reverse proxy
```

### Step 5: Build and Start

```bash
./run.sh rebuild --no-cache
```

First build takes 5-10 minutes. Subsequent builds are faster.

### Step 6: Create Admin User

```bash
./run.sh create-user \
  --username=admin \
  --email=admin@example.com \
  --password=YOUR_SECURE_PASSWORD \
  --admin
```

### Step 7: Access the Web UI

Open in your browser:
- **Web UI:** http://localhost:50080
- **API Docs:** http://localhost:40080/api/docs

Login with the email and password you created.

---

## Configuration Guide

Customize Ag3ntum by editing files in the `config/` directory.

### Configuration Files Overview

| File | Purpose | When to Modify |
|------|---------|----------------|
| `secrets.yaml` | API keys, credentials | API key setup |
| `api.yaml` | Server, ports, security | Remote access, HTTPS |
| `agent.yaml` | Claude model, limits | Model selection, timeouts |
| `external-mounts.yaml` | File sharing | Mount host directories |

### Changing Ports

Edit `config/api.yaml`:

```yaml
api:
  external_port: 40080  # Change API port

web:
  external_port: 50080  # Change Web UI port
```

Then restart:
```bash
./run.sh restart
```

### Configuring for Remote Access

1. Set your public hostname or IP:
   ```yaml
   server:
     hostname: "199.195.48.1"     # Your VPS IP
     # or
     hostname: "ag3ntum.example.com"  # Your domain
   ```

2. Open firewall ports:
   ```bash
   # Ubuntu (ufw)
   sudo ufw allow 50080/tcp
   sudo ufw allow 40080/tcp  # Optional: for direct API access
   ```

3. Restart:
   ```bash
   ./run.sh restart
   ```

### Setting Up HTTPS

Use a reverse proxy (nginx, traefik) for TLS termination:

1. Configure your reverse proxy to forward to:
   - Web UI: `http://127.0.0.1:50080`
   - API: `http://127.0.0.1:40080`

2. Update `config/api.yaml`:
   ```yaml
   server:
     hostname: "ag3ntum.example.com"
     protocol: "https"
     trusted_proxies:
       - "127.0.0.1"
   ```

Example nginx configuration:

```nginx
server {
    listen 443 ssl http2;
    server_name ag3ntum.example.com;

    ssl_certificate /etc/letsencrypt/live/ag3ntum.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ag3ntum.example.com/privkey.pem;

    # Web UI
    location / {
        proxy_pass http://127.0.0.1:50080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # API (SSE support requires longer timeouts)
    location /api/ {
        proxy_pass http://127.0.0.1:40080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;  # For SSE streaming
    }
}
```

### Changing the Claude Model

Edit `config/agent.yaml`:

```yaml
agent:
  default_model: claude-sonnet-4-5-20250929  # Change model
  thinking_tokens: 8000                       # Adjust thinking budget
  max_turns: 100                              # Max conversation turns
  timeout_seconds: 1800                       # Execution timeout (30 min)
```

**Available models:**
| Model | Speed | Cost | Best For |
|-------|-------|------|----------|
| `claude-haiku-4-5-20251001` | Fastest | Lowest | Quick tasks |
| `claude-sonnet-4-5-20250929` | Balanced | Medium | General use |
| `claude-opus-4-5-20251101` | Slowest | Highest | Complex tasks |

Add `:mode=thinking` suffix for extended thinking mode (e.g., `claude-sonnet-4-5-20250929:mode=thinking`).

### Mounting External Directories

Allow the agent to access host directories by creating `config/external-mounts.yaml`:

```bash
cp config/external-mounts.yaml.example config/external-mounts.yaml
```

Edit the file:

```yaml
global:
  # Read-only mounts (agent cannot modify)
  ro:
    - name: datasets
      host_path: /data/ml-datasets
      description: "ML training data"

  # Read-write mounts (agent can modify)
  rw:
    - name: output
      host_path: /data/output
      description: "Agent output directory"
```

Then rebuild:
```bash
./run.sh rebuild
```

The agent can access these at:
- Read-only: `./external/ro/datasets/`
- Read-write: `./external/rw/output/`

### Security Settings

`config/api.yaml` security section:

```yaml
security:
  # Security headers (X-Content-Type-Options, X-Frame-Options, etc.)
  enable_security_headers: true    # Recommended for production

  # Block requests with unexpected Host headers
  validate_host_header: true       # Recommended for production

  # Content Security Policy
  # "strict" - Restrictive (recommended)
  # "relaxed" - Allows inline scripts (development)
  # "disabled" - No CSP
  content_security_policy: "strict"

  # Additional allowed hostnames (besides server.hostname)
  additional_allowed_hosts: []
```

### Environment Variables

Override settings with environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `AG3NTUM_API_PORT` | 40080 | API port on host |
| `AG3NTUM_WEB_PORT` | 50080 | Web UI port on host |
| `AG3NTUM_IMAGE_TAG` | latest | Docker image tag |
| `AG3NTUM_UID_MODE` | isolated | UID security mode |

---

## Troubleshooting

### Build Fails with NumPy Error

**Error:** `RuntimeError: NumPy was built with baseline optimizations`

This happens on VPS with older CPUs (no SSE4.2). The Dockerfile handles this automatically. Rebuild on the target server:
```bash
./run.sh rebuild --no-cache
```

### Permission Denied

**Error:** `EACCES: permission denied` or `Permission denied: '/logs/backend.log'`

Fix directory ownership:
```bash
./run.sh rebuild --no-cache
# Or manually:
sudo chown -R 45045:45045 logs data users
```

### Cannot Access Web UI

1. Check containers are running:
   ```bash
   docker compose ps
   ```

2. Check logs:
   ```bash
   docker compose logs ag3ntum-web
   ```

3. Check firewall (Ubuntu):
   ```bash
   sudo ufw status
   ```

4. Verify port is correct:
   ```bash
   grep external_port config/api.yaml
   ```

### Login Fails

- Use your **email** (not username) to login
- Check API logs: `docker compose logs ag3ntum-api`
- Verify user was created: check output of create-user command

### Docker Daemon Not Running

**Error:** `Cannot connect to the Docker daemon`

Start Docker:
- **macOS:** Open Docker Desktop application
- **Linux:** `sudo systemctl start docker`

### Port Already in Use

**Error:** `Bind for 0.0.0.0:50080 failed: port is already allocated`

Find what's using the port:
```bash
lsof -i :50080
```

Either stop that process or change your port in `config/api.yaml`.

### Installer Stuck on Clone

If cloning takes too long:
```bash
# Clone manually with verbose output
git clone https://github.com/extractumio/ag3ntum.git
cd ag3ntum
./install.sh  # Will detect existing repo
```

---

## Useful Commands

| Command | Description |
|---------|-------------|
| `./run.sh build` | Build and start containers |
| `./run.sh restart` | Restart (reload code changes) |
| `./run.sh cleanup` | Stop and remove containers |
| `./run.sh rebuild --no-cache` | Full clean rebuild |
| `./run.sh shell` | Shell into API container |
| `./run.sh create-user` | Create a new user |
| `./run.sh test` | Run test suite |
| `docker compose logs -f` | View real-time logs |
| `docker compose ps` | Check container status |

---

## Next Steps

- **Mount external files:** See [Mounting External Directories](#mounting-external-directories)
- **Set up HTTPS:** See [Setting Up HTTPS](#setting-up-https)
- **Create more users:** `./run.sh create-user --help`
- **Full documentation:** [README.md](README.md)

---

**Questions?** [info@extractum.io](mailto:info@extractum.io)
