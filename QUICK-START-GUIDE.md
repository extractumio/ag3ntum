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

```bash
./run.sh rebuild --no-cache
```

This builds the Docker images and starts all services. First build takes 5-10 minutes.

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

Ag3ntum handles file permissions automatically:

| Platform | How Permissions Work |
|----------|---------------------|
| **macOS** | Docker Desktop handles permissions automatically |
| **Linux** | `run.sh` sets ownership to UID 45045 (container user) |

The container always runs as `ag3ntum_api` (UID 45045) for security and consistency.

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
| `./run.sh create-user` | Create a new user |
| `docker compose logs -f` | View logs |
| `docker compose ps` | Check container status |
| `docker compose exec ag3ntum-api bash` | Shell into API container |

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

**Cause:** Directories not owned by container user (UID 45045)

**Solution:** Re-run build (automatically fixes ownership on Linux):
```bash
./run.sh rebuild --no-cache
```

Or manually fix ownership:
```bash
sudo chown -R 45045:45045 logs data users
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

---

## Next Steps

- [README.md](README.md) - Full documentation and features
- Mount external files: `./run.sh build --mount-ro=/path/to/files:myfiles`
- Configure via `config/api.yaml` for custom ports

---

**Questions?** [info@extractum.io](mailto:info@extractum.io)
