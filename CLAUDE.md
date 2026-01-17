# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Ag3ntum** is a secure AI agent framework built on the Claude Agent SDK with security-first architecture. It provides both CLI and Web UI modes for executing AI agent tasks with a 5-layer defense-in-depth protection model.

- **Language**: Python 3.13+
- **License**: AGPL-3.0 (commercial license available)
- **Core SDK**: claude-agent-sdk 0.1.19
- **Architecture**: Dual-mode (CLI direct + FastAPI web server)

## Deployment & Development (Docker-First)

**Important**: The project is always deployed and tested locally via Docker. The `./deploy.sh` script is the main entry point for all operations.

### deploy.sh Commands

```bash
# Build and start containers (primary command)
./deploy.sh build

# Force rebuild without Docker cache (for significant changes)
./deploy.sh build --no-cache

# Full cleanup + rebuild (for major changes or troubleshooting)
./deploy.sh cleanup && ./deploy.sh build --no-cache
# Or use the shorthand:
./deploy.sh rebuild --no-cache

# Restart containers to reload code (preserves data, for small Python changes)
./deploy.sh restart

# Stop containers and remove images (full cleanup)
./deploy.sh cleanup

# Open shell in API container
./deploy.sh shell

# Create a new user
./deploy.sh create-user --username=USER --email=EMAIL --password=PASS [--admin]
```

### Running Tests via Docker

```bash
# Run all backend tests (default)
./deploy.sh test

# Run all tests (backend + core-tests + security)
./deploy.sh test --all

# Run specific test suites
./deploy.sh test --backend          # Backend tests only
./deploy.sh test --security         # Security/command filtering tests
./deploy.sh test --sandboxing       # Sandboxing tests

# Pattern matching for test files
./deploy.sh test "session*"         # Matches test_sessions.py, test_session_service.py
./deploy.sh test "auth|health"      # OR matching: test_auth.py and test_health.py
./deploy.sh test "session*|streaming"

# Pytest options
./deploy.sh test -k "ps_command"    # Filter by test name pattern
./deploy.sh test --security -x      # Stop on first failure
./deploy.sh test -v                 # Verbose output (default)
```

### When to Use Which Command

| Scenario | Command |
|----------|---------|
| First time setup | `./deploy.sh build` |
| Small Python code change | `./deploy.sh restart` |
| Config file changes | `./deploy.sh restart` |
| Dockerfile/requirements.txt changes | `./deploy.sh build --no-cache` |
| Major refactoring | `./deploy.sh cleanup && ./deploy.sh build --no-cache` |
| Troubleshooting build issues | `./deploy.sh rebuild --no-cache` |
| Running tests | `./deploy.sh test` |

### Local Development Setup (Optional)

For local development without Docker (limited use):
```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# CLI mode only (no sandboxing)
./venv/bin/python scripts/agent_cli.py --task "Your task here"
```

### Services & Ports

After `./deploy.sh build`:
- **API**: http://localhost:40080
- **Web UI**: http://localhost:50080
- **API Docs**: http://localhost:40080/api/docs
- **Redis**: localhost:46379 (internal)

## Architecture

### Entry Points
```
agent_cli.py → src/core/agent.py → execute_agent_task() → ClaudeAgent.run()
                                          ↓
Web UI (React) → FastAPI → agent_runner.py → execute_agent_task() → ClaudeAgent.run()
```

### Core Components (`src/core/`)

| Component | File | Purpose |
|-----------|------|---------|
| **ClaudeAgent** | `agent_core.py` | Main agent orchestrator, SDK integration, session lifecycle |
| **execute_agent_task** | `task_runner.py` | Unified entry point for CLI and API |
| **TaskExecutionParams** | `schemas.py` | Execution parameters dataclass |
| **PermissionManager** | `permission_profiles.py` | Tool access control, session context |
| **SessionManager** | `sessions.py` | File-based session CRUD, checkpoints |
| **TraceProcessor** | `trace_processor.py` | SDK message processing for tracing |
| **TracerBase** | `tracer.py` | Output tracing (ExecutionTracer for CLI, EventingTracer for SSE) |

### Security Components

| Layer | Component | File | Scope |
|-------|-----------|------|-------|
| 0 | Inbound WAF | `api/waf_filter.py` | Request size limits (API only) |
| 1 | Docker | `docker-compose.yml` | Host isolation |
| 2 | Bubblewrap | `sandbox.py` | Subprocess isolation (Ag3ntumBash) |
| 3 | PathValidator | `path_validator.py` | Python file tools validation |
| 4 | CommandSecurityFilter | `command_security.py` | Regex-based command blocking |

### Ag3ntum MCP Tools (`tools/ag3ntum/`)

Native Claude Code tools are **blocked**. All operations use Ag3ntum tools with built-in security:

| Tool | Security | Replaces |
|------|----------|----------|
| `mcp__ag3ntum__Read` | PathValidator | Read |
| `mcp__ag3ntum__Write` | PathValidator | Write |
| `mcp__ag3ntum__Edit` | PathValidator | Edit |
| `mcp__ag3ntum__Bash` | CommandFilter + Bubblewrap | Bash |
| `mcp__ag3ntum__Glob` | PathValidator | Glob |
| `mcp__ag3ntum__Grep` | PathValidator | Grep |
| `mcp__ag3ntum__LS` | PathValidator | LS |
| `mcp__ag3ntum__WebFetch` | Domain blocklist | WebFetch |

### API Layer (`src/api/`)

- **main.py**: FastAPI app with CORS, WAF middleware
- **routes/sessions.py**: Session management, task execution, SSE streaming
- **routes/auth.py**: JWT authentication
- **models.py**: Pydantic request/response models with WAF validators

### Services (`src/services/`)

- **agent_runner.py**: Background task execution via `execute_agent_task()`
- **session_service.py**: Session lifecycle (SQLite + file-based)
- **event_service.py**: SSE event streaming via asyncio queues

## Configuration Files

| File | Purpose |
|------|---------|
| `config/agent.yaml` | Agent settings (model, max_turns, timeout, skills) |
| `config/secrets.yaml` | API keys (ANTHROPIC_API_KEY) |
| `config/api.yaml` | API server config (host, port, CORS) |
| `config/security/permissions.yaml` | Tool enablement, sandbox config, session mounts |
| `config/security/tools-security.yaml` | PathValidator blocklists, network settings |
| `config/security/command-filtering.yaml` | 140+ regex patterns for command blocking |
| `config/security/upload-filtering.yaml` | File upload extension/MIME type whitelist/blacklist |

## Key Patterns

### Unified Task Execution
Both CLI and API use `execute_agent_task()` from `task_runner.py`:
```python
params = TaskExecutionParams(
    task="Your task",
    working_dir=Path("/path"),
    tracer=ExecutionTracer(),  # or BackendConsoleTracer()
)
result = await execute_agent_task(params)
```

### Permission Profiles
Permission configuration is loaded from `config/security/permissions.yaml`:
- `tools.enabled`: Allowed tools (Ag3ntum MCP tools)
- `tools.disabled`: Blocked tools (native Claude Code tools)
- `sandbox.*`: Bubblewrap configuration

### Tracer Pattern
Different tracers for different output modes:
- `ExecutionTracer`: Rich CLI output with spinners, boxes
- `BackendConsoleTracer`: Timestamped logging for API
- `EventingTracer`: Wraps tracer, emits SSE events to queue
- `NullTracer`: Silent (for testing)

### Session Structure
```
sessions/{session_id}/
├── session_info.json    # Metadata
├── agent.jsonl          # SDK message log
└── workspace/
    ├── output.yaml      # Agent output
    └── .claude/skills/  # Symlinks to skills
```

## Prompt Templates (`prompts/`)

- **system.j2**: Base system prompt
- **user.j2**: User prompt template
- **modules/**: Composable prompt components (identity, security, tools, skills)
- **roles/default.md**: Default agent role definition

## Web UI (`src/web_terminal_client/`)

React 18 + TypeScript + Vite terminal interface:
- **App.tsx**: Main component with routing
- **api.ts**: REST client for backend
- **sse.ts**: Server-Sent Events client
- **LoginPage.tsx**: JWT authentication

URLs:
- Web UI: http://localhost:50080
- API: http://localhost:40080
- API Docs: http://localhost:40080/api/docs

## Testing Notes

- Backend tests use pytest with `asyncio_mode = auto`
- E2E tests (`test_z_e2e_server.py`) require running server and API key
- Security tests validate command filtering (101 test cases)
- Test fixtures in `tests/backend/conftest.py` and `tests/core-tests/conftest.py`

## Important Implementation Details

1. **Fail-Closed Security**: If PathValidator or CommandSecurityFilter fails, operations are denied
2. **Filtered /proc**: Bubblewrap sandbox hides other processes (agents can't enumerate PIDs)
3. **Skills via Symlinks**: Skills in `workspace/.claude/skills/` symlink to actual directories
4. **Auto-Checkpointing**: Write/Edit tools trigger automatic file checkpoints
5. **Session-Scoped PathValidator**: Each session gets its own PathValidator instance

## Debugging & Testing Agent Execution

### ag3ntum_debug.py - Interactive Agent Testing

The `scripts/ag3ntum_debug.py` script is the primary tool for testing agent execution against the running Docker API. See `docs/how-to-debug-agent-with-ag3ntum_debug.md` for full documentation.

**Prerequisites**: Docker containers must be running (`./deploy.sh build`)

```bash
# Basic agent request
./venv/bin/python scripts/ag3ntum_debug.py -r "your task here" \
  --user "email@example.com" --password "yourpassword"

# With verbose output (shows all SSE events)
./venv/bin/python scripts/ag3ntum_debug.py -r "list files in workspace" \
  --user "email@example.com" --password "pass" --verbose

# Security-focused (shows only blocked operations)
./venv/bin/python scripts/ag3ntum_debug.py -r "read /etc/passwd" \
  --user "email@example.com" --password "pass" --security-only

# Dump session files after execution
./venv/bin/python scripts/ag3ntum_debug.py -r "create test.txt" \
  --user "email@example.com" --password "pass" --dump-session
```

**Options**:
| Option | Short | Description |
|--------|-------|-------------|
| `--request` | `-r` | Request to send (required) |
| `--user` | `-u` | User email for auth |
| `--password` | | Password for auth |
| `--host` | | API host (default: localhost) |
| `--port` | `-p` | API port (default: 40080) |
| `--verbose` | `-v` | Show all SSE events |
| `--security-only` | `-s` | Show only security events |
| `--dump-session` | `-d` | Dump session files after run |

**Exit Codes**: 0=success, 1=error, 2=security blocks detected

### Finding Session Artifacts

After running ag3ntum_debug.py, artifacts are at:
```
users/{username}/sessions/{session_id}/
├── agent.jsonl          # Complete event log
├── workspace/           # Files created by agent
└── output.md            # Agent's final response
```

### Docker Logs & Diagnostics

```bash
# View API container logs
docker logs project-ag3ntum-api-1 --tail 50

# Check permission denials
docker logs project-ag3ntum-api-1 2>&1 | grep -i "permission\|denied"

# Check security blocks
docker logs project-ag3ntum-api-1 2>&1 | grep -E "PathValidationError|BLOCKED|SANDBOX"

# View backend logs (inside container)
./deploy.sh shell
tail -f /logs/backend.log
```

### Analyzing Events (Redis & SQLite)

The system uses a **hybrid event architecture**:
- **Redis Pub/Sub**: Real-time event delivery to SSE subscribers (ephemeral, no persistence)
- **SQLite**: Permanent event storage for replay and history

**Key files**:
- `src/services/redis_event_hub.py` - Redis Pub/Sub implementation
- `src/services/event_service.py` - SQLite event persistence
- `src/api/routes/sessions.py:541-656` - SSE streaming with replay logic

#### SQLite Event Analysis

```bash
# Database location
Project/data/ag3ntum.db

# List tables
sqlite3 Project/data/ag3ntum.db ".tables"
# Output: events    sessions    tokens    users

# Check event schema
sqlite3 Project/data/ag3ntum.db ".schema events"

# Count events for a session
sqlite3 Project/data/ag3ntum.db \
  "SELECT COUNT(*) FROM events WHERE session_id = 'SESSION_ID';"

# Event breakdown by type
sqlite3 Project/data/ag3ntum.db \
  "SELECT event_type, COUNT(*) as count FROM events
   WHERE session_id = 'SESSION_ID'
   GROUP BY event_type ORDER BY count DESC;"

# List events with truncated data
sqlite3 Project/data/ag3ntum.db \
  "SELECT id, sequence, event_type, substr(data, 1, 60) as data_preview, timestamp
   FROM events WHERE session_id = 'SESSION_ID' ORDER BY sequence;"

# Full event data (JSON)
sqlite3 Project/data/ag3ntum.db \
  "SELECT data FROM events WHERE session_id = 'SESSION_ID' AND event_type = 'message';"
```

#### Redis Event Analysis

Redis uses Pub/Sub (ephemeral) - events are only delivered to connected subscribers.

```bash
# Find Redis container
docker ps --format "{{.Names}} {{.Image}}" | grep redis
# Output: project-redis-1 redis:7-alpine

# Check if Redis is responding
docker exec project-redis-1 redis-cli PING

# Check active Pub/Sub channels
docker exec project-redis-1 redis-cli PUBSUB CHANNELS "session:*"

# Check subscriber count for a session channel
docker exec project-redis-1 redis-cli PUBSUB NUMSUB "session:SESSION_ID:events"

# Scan for any ag3ntum keys (if using Lists instead of Pub/Sub)
docker exec project-redis-1 redis-cli SCAN 0 MATCH "ag3ntum:*" COUNT 100
```

#### Event Flow Architecture

```
Agent Execution → EventingTracer.emit_event()
                       ↓
              1. Publish to Redis Pub/Sub (~1ms)
                       ↓
              2. Persist to SQLite (~5-50ms)

SSE Client Connects → subscribe to Redis Pub/Sub
                           ↓
                      Replay from SQLite (with 10-event overlap buffer)
                           ↓
                      Stream live events from Redis
                           ↓
                      Deduplicate by sequence number
```

**Why Redis shows 0 events**: Redis Pub/Sub is ephemeral. Once events are delivered to subscribers, they're gone. If no subscribers are connected, events are published but not stored. SQLite is the source of truth for event history.

**Late-joining clients**: The SSE endpoint (`GET /sessions/{id}/events`) handles this by:
1. Subscribing to Redis first (catches future events)
2. Replaying all events from SQLite (with 10-event overlap buffer)
3. Deduplicating by sequence number
4. All clients see identical content regardless of when they connect
