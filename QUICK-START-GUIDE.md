# Ag3ntum Quick Start Guide

Deploy Ag3ntum on your VPS in 5 minutes.

## Prerequisites

- **Docker** (20.10+) and **Docker Compose** (v2)
- **Git**
- **2GB RAM** minimum (4GB recommended)
- **Anthropic API key** from [console.anthropic.com](https://console.anthropic.com/settings/keys)

## Deployment Steps

### 1. Clone the Repository

```bash
git clone https://github.com/extractumio/ag3ntum.git
cd ag3ntum
```

### 2. Configure API Key

Create the secrets file with your Anthropic API key:

```bash
cat > config/secrets.yaml << 'EOF'
anthropic_api_key: sk-ant-api03-YOUR_KEY_HERE
EOF
```

Replace `sk-ant-api03-YOUR_KEY_HERE` with your actual key.

### 3. Build and Start

**On VPS/Production:**
```bash
# Set file ownership to current user (recommended)
HOST_UID=$(id -u) HOST_GID=$(id -g) ./run.sh rebuild --no-cache
```

**On Local/Development:**
```bash
./run.sh rebuild --no-cache
```

This builds the Docker images and starts all services. First build takes 5-10 minutes.

> **Note:** Building takes longer on older VPS servers due to NumPy compilation for CPU compatibility.

### 4. Create Admin User

```bash
./run.sh create-user \
  --username=admin \
  --email=admin@example.com \
  --password=YOUR_SECURE_PASSWORD \
  --admin
```

### 5. Access the Web UI

Open in browser: `http://YOUR_VPS_IP:50080/`

Login with the email and password you just created.

### 6. Run Your First Query

Type a prompt in the chat interface and press Enter. The agent will execute in a sandboxed environment.

---

## File Permissions

Ag3ntum uses Docker volume mounts for configuration and data. To ensure proper file ownership:

### VPS Deployment (Recommended)

Pass your host user ID to Docker Compose:

```bash
# One-time setup: export in your shell profile (~/.bashrc or ~/.zshrc)
export HOST_UID=$(id -u)
export HOST_GID=$(id -g)

# Then run normally
docker compose up -d
```

Or pass inline:
```bash
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d
```

### Why This Matters

| Without HOST_UID | With HOST_UID |
|------------------|---------------|
| Files owned by UID 45045 | Files owned by your user |
| May need sudo to edit | Normal user access |
| Permission errors possible | Clean permissions |

---

## Firewall Configuration

Open port **50080** (Web UI) on your VPS firewall:

```bash
# Ubuntu/Debian (ufw)
sudo ufw allow 50080/tcp

# CentOS/RHEL (firewalld)
sudo firewall-cmd --add-port=50080/tcp --permanent
sudo firewall-cmd --reload
```

Port 40080 (API) is optional—only needed for direct API access.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST_UID` | 45045 | Container user ID (set to `$(id -u)` on VPS) |
| `HOST_GID` | 45045 | Container group ID (set to `$(id -g)` on VPS) |
| `AG3NTUM_API_PORT` | 40080 | API port on host |
| `AG3NTUM_WEB_PORT` | 50080 | Web UI port on host |
| `AG3NTUM_IMAGE_TAG` | latest | Docker image tag |

---

## Useful Commands

| Command | Description |
|---------|-------------|
| `./run.sh rebuild --no-cache` | Full rebuild (clean) |
| `./run.sh restart` | Restart containers (keeps data) |
| `./run.sh cleanup` | Stop and remove containers |
| `./run.sh shell` | Open shell inside container |
| `docker compose logs -f` | View logs |
| `docker compose ps` | Check container status |

---

## Troubleshooting

### Build fails with NumPy error

**Error:** `RuntimeError: NumPy was built with baseline optimizations (X86_V2)`

**Cause:** VPS has an older CPU (common with QEMU/KVM virtualization)

**Solution:** The Dockerfile already handles this by compiling NumPy from source. If you see this error, ensure you're using the latest Dockerfile.

### Permission denied errors

**Error:** `EACCES: permission denied` when installing npm packages

**Solution:** Set HOST_UID/HOST_GID environment variables:
```bash
HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose up -d
```

### Cannot access web UI

- Verify containers are running: `docker compose ps`
- Check logs: `docker compose logs ag3ntum-web`
- Check firewall: `sudo ufw status` or `firewall-cmd --list-all`
- Ensure port 50080 is open

### Login fails

- Verify user was created: check output of `create-user` command
- Use **email** (not username) to login
- Check API logs: `docker compose logs ag3ntum-api`

### Files have wrong ownership

If files are owned by UID 45045 instead of your user:
```bash
# Fix ownership (run from project directory)
sudo chown -R $(id -u):$(id -g) config data logs src users
```

---

## Next Steps

- [README.md](README.md) - Full documentation and features
- Mount external files: `./run.sh build --mount-ro=/path/to/files:myfiles`
- Configure via `config/api.yaml` for custom ports

---

**Questions?** [info@extractum.io](mailto:info@extractum.io)
