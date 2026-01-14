# Ag3ntum Quick Start - Web UI

## First Time Setup (Local Development)

```bash
# 1. Install Web UI dependencies
cd Project
./deploy/setup_web_ui.sh

# 2. Ensure Python venv is ready
source venv/bin/activate
pip install -r requirements.txt
```

> **Note**: Redis is NOT required for local development. The event streaming uses in-memory queues.

---

## Running the Full Stack

### Option 1: VSCode (Recommended)

1. Press `F5` in VSCode
2. Select **"Full Stack (Backend + Web UI)"**
3. Browser opens automatically to http://localhost:50080

### Option 2: Manual (Local)

```bash
# Terminal 1 - Backend
cd Project && source venv/bin/activate
uvicorn src.api.main:app --host 0.0.0.0 --port 40080 --reload

# Terminal 2 - Frontend
cd Project/src/web_terminal_client
npm run dev
```

### Option 3: Docker Compose

```bash
cd Project

# Build and start all services (includes Redis)
docker-compose up --build

# Or run in detached mode
docker-compose up -d --build

# View logs
docker-compose logs -f

# Stop all services
docker-compose down
```

Docker Compose automatically starts:
- **ag3ntum-api** - FastAPI backend on port 40080
- **ag3ntum-web** - React frontend on port 50080
- **redis** - Redis 7 on port 46379 (localhost only)

---

## Access URLs

| Service | URL |
|---------|-----|
| Web UI | http://localhost:50080 |
| Backend API | http://localhost:40080 |
| API Docs | http://localhost:40080/api/docs |

---

## VSCode Launch Configurations

| Configuration | Description |
|--------------|-------------|
| **Full Stack (Backend + Web UI)** | Runs both services together |
| **Backend API Server** | Runs only FastAPI backend |
| **Web UI (React/Vite)** | Runs only React frontend |
| **HTTP Agent Client** | Runs agent_http.py client |

---

## Environment Configuration

Key ports and settings are configured in:

| File | Purpose |
|------|---------|
| `config/api.yaml` | Backend API port (40080), CORS origins, JWT settings |
| `docker-compose.yml` | Docker service ports, Redis configuration |
| `src/web_terminal_client/vite.config.ts` | Frontend port (50080), proxy settings |

### Docker Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AG3NTUM_API_PORT` | 40080 | Backend API external port |
| `AG3NTUM_WEB_PORT` | 50080 | Web UI external port |
| `AG3NTUM_IMAGE_TAG` | latest | Docker image tag |

---

## Documentation

- **Architecture**: `docs/current_architecture.md`
- **SSE Implementation**: `docs/current_sse.md`

---

## Troubleshooting

### Kill Processes on Ports

```bash
lsof -ti:40080 | xargs kill -9  # Backend
lsof -ti:50080 | xargs kill -9  # Frontend
```

### Reinstall Frontend Dependencies

```bash
cd Project/src/web_terminal_client
rm -rf node_modules && npm install
```

### Docker Issues

```bash
# Remove all containers and volumes
docker-compose down -v

# Rebuild without cache
docker-compose build --no-cache

# Check Redis connection (from host)
redis-cli -p 46379 ping
```
