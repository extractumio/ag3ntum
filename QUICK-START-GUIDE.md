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

This builds the Docker images and starts all services. First build takes 3-5 minutes.

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

## Useful Commands

| Command | Description |
|---------|-------------|
| `./run.sh rebuild --no-cache` | Full rebuild (clean) |
| `./run.sh restart` | Restart containers (keeps data) |
| `./run.sh cleanup` | Stop and remove containers |
| `./run.sh shell` | Open shell inside container |
| `docker compose logs -f` | View logs |

---

## Troubleshooting

**Build fails:**
- Ensure Docker is running: `docker info`
- Check disk space: `df -h`

**Cannot access web UI:**
- Verify containers are running: `docker compose ps`
- Check firewall: `sudo ufw status` or `firewall-cmd --list-all`

**Login fails:**
- Verify user was created: check output of `create-user` command
- Use email (not username) to login

---

## Next Steps

- [README.md](README.md) - Full documentation and features
- Mount external files: `./run.sh build --mount-ro=/path/to/files:myfiles`
- Configure via `config/api.yaml` for custom ports

---

**Questions?** [info@extractum.io](mailto:info@extractum.io)
