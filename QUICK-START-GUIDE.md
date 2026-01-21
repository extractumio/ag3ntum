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

Ag3ntum **automatically detects** the best user ID for your environment:

| Environment | Auto-detected UID | Why |
|-------------|-------------------|-----|
| **Running as root** | 45045 (service user) | Production isolation |
| **Running as regular user** | Your UID | No permission issues |
| **macOS** | Your UID | Docker Desktop handles mapping |

### How It Works

1. `./run.sh build` auto-detects the appropriate UID/GID
2. Creates required directories with correct ownership
3. Saves settings to `.env` for future `docker compose` commands

### Override (Optional)

To force a specific UID:

```bash
HOST_UID=45045 HOST_GID=45045 ./run.sh rebuild --no-cache
```

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

**Cause:** VPS has an older CPU without SSE4.2 support (common with QEMU/KVM virtualization)

**Solution:** This is automatically handled during Docker build. The Dockerfile detects CPU capabilities and installs compatible package versions:
- Modern CPUs (SSE4.2): numpy 2.x, pandas 2.2+
- Legacy CPUs (no SSE4.2): numpy 1.26.4, pandas 2.1.4

If you see this error at runtime, rebuild the image **on the target server**:
```bash
./run.sh rebuild --no-cache
```

### Permission denied errors

**Error:** `EACCES: permission denied` or `Permission denied: '/logs/backend.log'`

**Cause:** Directories owned by root but container running as non-root user

**Solution:** Re-run build (automatically fixes ownership):
```bash
./run.sh rebuild --no-cache
```

Or manually fix ownership:
```bash
sudo chown -R 45045:45045 logs data src config users
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

If files are owned by UID 45045 instead of your user (this is normal for production):
```bash
# Option 1: Change to your user (development)
sudo chown -R $(id -u):$(id -g) config data logs src users

# Option 2: Rebuild with your UID (development)
HOST_UID=$(id -u) HOST_GID=$(id -g) ./run.sh rebuild --no-cache
```

> **Note:** For production deployments, UID 45045 ownership is intentional and secure.

---

## Next Steps

- [README.md](README.md) - Full documentation and features
- Mount external files: `./run.sh build --mount-ro=/path/to/files:myfiles`
- Configure via `config/api.yaml` for custom ports

---

**Questions?** [info@extractum.io](mailto:info@extractum.io)
