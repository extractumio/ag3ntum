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

## Production Deployment

### Configure Public Access

Edit `config/api.yaml` to set your public hostname:

```yaml
server:
  # Your public IP or domain
  hostname: "199.195.48.1"        # VPS IP
  # hostname: "ag3ntum.example.com"  # Or domain name

  # Protocol (http or https)
  protocol: "http"
```

Then restart:
```bash
./run.sh restart
```

### HTTPS with Reverse Proxy

For HTTPS, use nginx or traefik as a TLS-terminating reverse proxy:

```yaml
# config/api.yaml
server:
  hostname: "ag3ntum.example.com"
  protocol: "https"
  trusted_proxies:
    - "127.0.0.1"
```

Example nginx config:
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

    # API
    location /api/ {
        proxy_pass http://127.0.0.1:40080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Security Settings

The following security features are enabled by default in `config/api.yaml`:

```yaml
security:
  # Security headers (X-Content-Type-Options, X-Frame-Options, etc.)
  enable_security_headers: true

  # Host header validation (prevents host header injection)
  validate_host_header: true

  # Content Security Policy: "strict", "relaxed", or "disabled"
  content_security_policy: "strict"

  # Additional allowed hosts (for accessing via multiple hostnames)
  additional_allowed_hosts: []
```

### Deployment Checklist

- [ ] Set `server.hostname` to your public IP or domain
- [ ] Configure firewall (ports 50080, 40080)
- [ ] Set up HTTPS via reverse proxy for production
- [ ] Use strong passwords for admin users
- [ ] Back up `config/secrets.yaml` and `data/` directory
- [ ] Configure `trusted_proxies` if behind a reverse proxy

---

## Next Steps

- [README.md](README.md) - Full documentation and features
- Mount external files: `./run.sh build --mount-ro=/path/to/files:myfiles`
- Configure via `config/api.yaml` for custom ports and security

---

**Questions?** [info@extractum.io](mailto:info@extractum.io)
