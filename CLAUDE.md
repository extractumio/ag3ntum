# CLAUDE.md - Ag3ntum Reference Guide

This file provides guidance to Claude Code when working with this repository. Use this as your primary reference for project structure, common tasks, and avoiding pitfalls.

## Quick Reference

### Most Common Commands
```bash
./run.sh build              # Build and start containers
./run.sh restart            # Restart containers (for code changes)
./run.sh test               # Run backend tests
./run.sh test --all         # Run all tests (backend + security + sandboxing)
./run.sh shell              # Open shell in API container
./run.sh cleanup            # Stop and remove containers
```

### Documentation and specs
Consult the architectural documentation in @DOCUMENTS/TECHNICAL whenever you need to fix bugs or design a new feature.

### Key URLs (after `./run.sh build`)
- **Web UI**: http://localhost:50080
- **API**: http://localhost:40080
- **API Docs**: http://localhost:40080/api/docs

### Project Identity
- **Name**: Ag3ntum
- **Language**: Python 3.13+
- **License**: AGPL-3.0
- **Core SDK**: claude-agent-sdk 0.1.20
- **Security Model**: 6-layer defense-in-depth with UID isolation

---

## Project Structure Overview

```
Project/
├── config/                  # All configuration files
│   ├── agent.yaml           # Agent settings (model, max_turns, timeout)
│   ├── api.yaml             # API server config (host, port, CORS)
│   ├── secrets.yaml         # API keys (ANTHROPIC_API_KEY)
│   └── security/            # Security configurations
│       ├── permissions.yaml       # Tool enablement, sandbox config
│       ├── tools-security.yaml    # PathValidator, secrets scanning
│       ├── command-filtering.yaml # 140+ regex patterns (16 categories)
│       ├── upload-filtering.yaml  # File upload filters
│       ├── seccomp-isolated.json  # Seccomp profile (UID 50000-60000)
│       └── seccomp-direct.json    # Seccomp profile (direct UID mode)
├── src/
│   ├── core/                # Core agent logic (25+ files)
│   ├── api/                 # FastAPI application
│   ├── services/            # Business logic services
│   ├── security/            # Security utilities (secrets scanner)
│   └── web_terminal_client/ # React Web UI
├── tools/ag3ntum/           # Custom MCP tools (9 tools)
├── prompts/                 # Jinja2 prompt templates
├── tests/                   # Test suites
├── scripts/                 # CLI tools
├── docs/                    # Documentation
└── skills/                  # Skill definitions
```

---

## Source Code Index

### Core Components (`src/core/`)

| File | Class/Function | Purpose | When to Modify |
|------|----------------|---------|----------------|
| `agent_core.py` | `ClaudeAgent` | Main agent orchestrator, SDK integration | Agent lifecycle changes |
| `task_runner.py` | `execute_agent_task()` | **Unified entry point** for CLI and API | Execution flow changes |
| `schemas.py` | `TaskExecutionParams` | Execution parameters dataclass | Adding execution params |
| `permission_profiles.py` | `PermissionManager` | Tool access control, session context | Permission logic |
| `sessions.py` | `SessionManager` | File-based session CRUD, checkpoints | Session handling |
| `sandbox.py` | `SandboxExecutor`, `SandboxConfig` | Bubblewrap sandbox + UID dropping | Sandbox changes |
| `uid_security.py` | `UIDSecurityConfig` | UID/GID validation, seccomp generation | UID isolation logic |
| `path_validator.py` | `Ag3ntumPathValidator` | File path validation for tools | Path security |
| `command_security.py` | `CommandSecurityFilter` | Regex-based command blocking | Command filtering |
| `tracer.py` | `TracerBase`, `ExecutionTracer` | Output tracing for CLI/API | Output formatting |
| `trace_processor.py` | `TraceProcessor` | SDK message processing | Event processing |

### API Layer (`src/api/`)

| File | Purpose | When to Modify |
|------|---------|----------------|
| `main.py` | FastAPI app factory, middleware setup | Adding middleware |
| `routes/sessions.py` | Session CRUD, task execution, SSE streaming | Session endpoints |
| `routes/auth.py` | JWT authentication | Auth flow |
| `routes/files.py` | File explorer endpoints | File operations |
| `routes/health.py` | Health check, config endpoint | Status endpoints |
| `security_middleware.py` | HTTP headers, CSP, host validation | Web security |
| `waf_filter.py` | Request size limits, DoS prevention | Input validation |
| `models.py` | Pydantic request/response models | API contracts |
| `deps.py` | Dependency injection (JWT, DB) | DI setup |

### Security Components (`src/security/`)

| File | Class | Purpose |
|------|-------|---------|
| `sensitive_data_scanner.py` | `SensitiveDataScanner` | Secrets scanning and redaction |
| `scanner_config.py` | | Scanner configuration loading |

### Services (`src/services/`)

| File | Class | Purpose |
|------|-------|---------|
| `agent_runner.py` | `AgentRunner` | Background task execution |
| `session_service.py` | `SessionService` | Session lifecycle (SQLite + files) |
| `event_service.py` | `EventService` | SSE event persistence |
| `redis_event_hub.py` | `RedisEventHub` | Redis Pub/Sub for real-time events |
| `auth_service.py` | `AuthService` | JWT authentication |
| `user_service.py` | `UserService` | User CRUD operations |

### MCP Tools (`tools/ag3ntum/`)

| Tool | File | Security | Replaces |
|------|------|----------|----------|
| `mcp__ag3ntum__Read` | `ag3ntum_read/tool.py` | PathValidator | Read |
| `mcp__ag3ntum__Write` | `ag3ntum_write/tool.py` | PathValidator | Write |
| `mcp__ag3ntum__Edit` | `ag3ntum_edit/tool.py` | PathValidator | Edit |
| `mcp__ag3ntum__Bash` | `ag3ntum_bash/tool.py` | CommandFilter + Bubblewrap + UID | Bash |
| `mcp__ag3ntum__Glob` | `ag3ntum_glob/tool.py` | PathValidator | Glob |
| `mcp__ag3ntum__Grep` | `ag3ntum_grep/tool.py` | PathValidator | Grep |
| `mcp__ag3ntum__LS` | `ag3ntum_ls/tool.py` | PathValidator | LS |
| `mcp__ag3ntum__WebFetch` | `ag3ntum_webfetch/tool.py` | Domain blocklist | WebFetch |
| `mcp__ag3ntum__ReadDocument` | `ag3ntum_read_document/tool.py` | Size limits | *New* |

**IMPORTANT**: Native Claude Code tools are **BLOCKED** via `tools.disabled` in `permissions.yaml`. All operations must go through `mcp__ag3ntum__*` tools.

### Web Terminal Client (`src/web_terminal_client/`)

React 18 + TypeScript 5.6 application with Vite 5.4 build system (~10,278 lines, 47 files).

| File/Directory | Purpose | When to Modify |
|----------------|---------|----------------|
| `src/App.tsx` | Main orchestrator (~1200 lines), session state, SSE | Core UI flow changes |
| `src/api.ts` | API client (100+ functions) | Adding API calls |
| `src/apiCache.ts` | TTL cache with stale-while-revalidate | Cache behavior |
| `src/sse.ts` | SSE connection, polling fallback | Event streaming |
| `src/AuthContext.tsx` | JWT authentication context | Auth flow |
| `src/ConnectionManager.ts` | Connection state machine | Connection resilience |
| `src/hooks/` | Custom hooks (6 files) | State logic extraction |
| `src/components/messages/` | Message rendering (12 files) | Chat display |
| `src/FileExplorer.tsx` | File browser widget | File management UI |
| `src/FileViewer.tsx` | File preview modal | File preview |
| `src/MarkdownRenderer.tsx` | Markdown rendering | Markdown display |
| `src/styles.css` | CSS variables (dark theme) | Styling |

**Key Hooks:**
- `useSSEConnection` - SSE streaming, reconnection, event deduplication
- `useSessionManager` - Session CRUD, history, statistics
- `useUIState` - Local UI state (collapse, modals)
- `useFileOperations` - File upload, download, delete

**Connection States:** `connected` → `reconnecting` → `polling` → `degraded`

**SSE Events:** `agent_start`, `tool_start`, `tool_complete`, `message`, `thinking`, `subagent_*`, `agent_complete`, `error`, `cancelled`

**Frontend Commands:**
```bash
cd src/web_terminal_client
npm run dev       # Dev server (port 50080)
npm run build     # Production build
npm run test      # Run tests
```

---

## Configuration Files Index

### Agent Configuration (`config/agent.yaml`)
```yaml
model: claude-sonnet-4-20250514  # Model to use
max_turns: 100                    # Max conversation turns
timeout_seconds: 1800             # Global timeout
role: default                     # Role from prompts/roles/
```

### Security Configuration (`config/security/`)

| File | Purpose | Key Settings |
|------|---------|--------------|
| `permissions.yaml` | Tool enablement, sandbox | `tools.enabled`, `tools.disabled`, `sandbox.*` |
| `tools-security.yaml` | PathValidator, secrets | `path_validator.blocklist`, `sensitive_data.*` |
| `command-filtering.yaml` | Command blocking | 140+ patterns in 16 categories |
| `upload-filtering.yaml` | File uploads | Blocked extensions, MIME types |

### Secrets Configuration (`config/secrets.yaml`)
```yaml
ANTHROPIC_API_KEY: "sk-ant-..."
sandboxed_envs:              # Per-user API keys (visible only in sandbox)
  OPENAI_API_KEY: "sk-..."
```

---

## Security Architecture (6-Layer Model)

Understanding the security layers is critical for correct modifications:

| Layer | Component | File(s) | Scope |
|-------|-----------|---------|-------|
| **0** | Inbound WAF | `api/waf_filter.py` | API requests only |
| **1** | Docker | `docker-compose.yml` | Container isolation |
| **2** | Bubblewrap + UID | `core/sandbox.py`, `core/uid_security.py` | Subprocess only (Bash) |
| **3** | Ag3ntum Tools | `tools/ag3ntum/*`, `core/path_validator.py` | File/command ops |
| **4** | Command Filter | `core/command_security.py` | Bash commands |
| **5** | Security Middleware | `api/security_middleware.py` | HTTP responses |
| **6** | Prompts | `prompts/modules/security.j2` | LLM guidance |

### UID Security (Layer 2)

Each user runs under their own UID. This is OS-enforced, not prompt-based.

**UID/GID Range Definitions:**
| Range | Purpose | Notes |
|-------|---------|-------|
| 1-999 | System accounts | Reserved for OS services |
| 2000-49999 | Legacy users | Still valid, no new allocations |
| 45045 | API user | Special UID for API process (in legacy range) |
| 50000-60000 | Isolated users | **New allocations** - sandbox user UIDs |

**Key files**:
- `src/core/uid_security.py` - UID validation, seccomp profile generation
- `config/security/seccomp-isolated.json` - Kernel-level UID restrictions
- `tools/ag3ntum/ag3ntum_bash/tool.py` - Applies `--uid`/`--gid` flags

**Modes**:
- `ISOLATED` (default): UIDs 50000-60000, no host mapping
- `DIRECT`: UIDs 1000-65533, maps to host users

### Secrets Scanning (Layer 5)

File Explorer automatically redacts sensitive data:
- **File**: `src/security/sensitive_data_scanner.py`
- **Config**: `config/security/tools-security.yaml` → `sensitive_data.*`
- **Patterns**: API keys, tokens, passwords, private keys

---

## Testing Guide

### Running Tests

```bash
# All backend tests (default)
./run.sh test

# All tests (backend + security + sandboxing)
./run.sh test --all

# Specific suites
./run.sh test --backend          # Backend only
./run.sh test --security         # Command filtering (101 tests)
./run.sh test --sandboxing       # Sandboxing tests

# Pattern matching
./run.sh test "session*"         # Matches session-related tests
./run.sh test "auth|health"      # OR matching
./run.sh test -k "ps_command"    # Pytest filter by test name
```

### Test File Locations

| Suite | Location | Purpose |
|-------|----------|---------|
| Backend | `tests/backend/` | API, services, routes |
| Core | `tests/core-tests/` | Core components |
| Security | `tests/security/` | Command filtering, path validation |
| Sandboxing | `tests/sandboxing/` | Bubblewrap tests |
| E2E | `tests/backend/test_z_e2e_server.py` | Full integration |

### Test Fixtures

- `tests/backend/conftest.py` - API test fixtures, mock clients
- `tests/core-tests/conftest.py` - Core test fixtures
- All tests use `asyncio_mode = auto`

---

## Common Tasks & How-To

### Adding a New API Endpoint

1. Create route function in `src/api/routes/{module}.py`
2. Add Pydantic models to `src/api/models.py`
3. Register route in `src/api/main.py` → `create_app()`
4. Add tests in `tests/backend/test_{module}.py`

### Adding a New Ag3ntum Tool

1. Create directory: `tools/ag3ntum/ag3ntum_{name}/`
2. Create `__init__.py` and `tool.py`
3. Implement tool class inheriting from appropriate base
4. Add PathValidator integration if file-based
5. Register in `tools/ag3ntum/__init__.py`
6. Add to `config/security/permissions.yaml` → `tools.enabled`

### Modifying Security Rules

**Command filtering**: Edit `config/security/command-filtering.yaml`
- Each rule has: `pattern`, `action` (block/record), `exploit` (test case)
- Run `./run.sh test --security` to validate

**Path validation**: Edit `config/security/tools-security.yaml`
- `path_validator.blocklist` - Blocked patterns
- `path_validator.readonly_paths` - Read-only paths

### Adding a New Secret Type for Scanning

1. Edit `config/security/tools-security.yaml`
2. Add pattern to `sensitive_data.custom_patterns`
3. Or add detect-secrets plugin to `sensitive_data.detect_secrets_plugins`

### Debugging Agent Execution

```bash
# Basic request
./venv/bin/python scripts/ag3ntum_debug.py -r "your task" \
  --user "email@example.com" --password "pass"

# Verbose (all events)
./venv/bin/python scripts/ag3ntum_debug.py -r "task" -v

# Security only (blocked operations)
./venv/bin/python scripts/ag3ntum_debug.py -r "task" -s

# Dump session files
./venv/bin/python scripts/ag3ntum_debug.py -r "task" -d
```

### Checking Logs

```bash
# API container logs
docker logs project-ag3ntum-api-1 --tail 100

# Permission denials
docker logs project-ag3ntum-api-1 2>&1 | grep -i "denied\|blocked"

# Inside container
./run.sh shell
tail -f /logs/backend.log
```

---

## Diagnostics & Troubleshooting

### Session Storage

Sessions are stored as files on disk for SDK compatibility and in SQLite for fast queries.

**File Location**: `users/{username}/sessions/{session_id}/`

```bash
# List all sessions for a user
ls users/greg/sessions/

# Session directory structure
users/greg/sessions/20260125_150542_1c4fce4f/
├── session_info.json    # Metadata, resume_id, cumulative stats
├── agent.jsonl          # Complete SDK event log (JSONL format)
└── workspace/
    ├── output.yaml      # Agent execution output
    └── external/        # External mount symlinks
```

**Inspecting Sessions**:
```bash
# View session metadata (status, cost, turns, resume_id)
cat users/greg/sessions/20260125_150542_1c4fce4f/session_info.json | jq .

# Watch SDK events in real-time during execution
tail -f users/greg/sessions/20260125_150542_1c4fce4f/agent.jsonl

# Search for tool errors across all sessions
grep -r "error" users/greg/sessions/*/agent.jsonl
```

**Key Fields in `session_info.json`**:
| Field | Purpose |
|-------|---------|
| `status` | `COMPLETE`, `PARTIAL`, `FAILED`, `ERROR` |
| `resume_id` | Claude session ID for resuming |
| `total_cost_usd` | Cost for this execution |
| `cumulative_cost_usd` | Total cost across all runs |
| `num_turns` | API round-trips this execution |

### Log Files

**Location**: `logs/` directory

| File | Content | Use Case |
|------|---------|----------|
| `backend.log` | API server, routes, services | API/backend debugging |
| `agent_cli.log` | CLI agent execution | CLI debugging |

**Log Rotation**: 10MB max, 5 backup files (`.1`, `.2`, etc.)

**Viewing Logs**:
```bash
# Real-time backend log (inside container)
./run.sh shell
tail -f /logs/backend.log

# From host via Docker
docker logs project-ag3ntum-api-1 --tail 100 -f

# Filter for specific patterns
grep "ERROR\|Exception" logs/backend.log
grep "session_id.*20260125" logs/backend.log
grep -i "denied\|blocked" logs/backend.log  # Security denials
```

**Configured Loggers**: `src.api`, `src.services`, `src.core`, `src.db`, `ag3ntum`, `tools.ag3ntum`, `uvicorn`, `fastapi`

### Database (SQLite)

**Location**: `data/ag3ntum.db`

**Tables**:
| Table | Purpose |
|-------|---------|
| `users` | User accounts, `linux_uid` for sandbox isolation |
| `sessions` | Session metadata, status, task, timestamps |
| `events` | SSE events (persistent storage for replay) |
| `tokens` | Encrypted user tokens/credentials |

**Querying the Database**:
```bash
# Open database (inside container or with sqlite3 installed)
sqlite3 data/ag3ntum.db

# List sessions for a user
SELECT id, status, task, created_at FROM sessions
WHERE user_id = (SELECT id FROM users WHERE username = 'greg')
ORDER BY created_at DESC LIMIT 10;

# Check session status
SELECT status, num_turns, total_cost_usd, duration_ms
FROM sessions WHERE id = '20260125_150542_1c4fce4f';

# Count events per session
SELECT session_id, COUNT(*) as event_count
FROM events GROUP BY session_id ORDER BY event_count DESC;

# View recent events for a session
SELECT sequence, event_type, timestamp, substr(data, 1, 100) as preview
FROM events WHERE session_id = '20260125_150542_1c4fce4f'
ORDER BY sequence DESC LIMIT 20;

# Find error events
SELECT session_id, data FROM events
WHERE event_type = 'error' ORDER BY timestamp DESC LIMIT 5;
```

**User UID lookup** (for sandbox debugging):
```sql
SELECT username, linux_uid FROM users WHERE linux_uid BETWEEN 50000 AND 60000;
```

### SSE Event System

Events flow through a dual system for real-time delivery and persistence:

```
Agent Execution → Redis Stream (real-time) → SSE to Browser
                ↘ SQLite events table (persistent) ↗ Polling fallback
```

**Event Types**:
| Type | Meaning |
|------|---------|
| `agent_start` | Session initialized |
| `message` | Agent text output |
| `thinking` | Extended thinking content |
| `tool_start` / `tool_complete` | Tool execution |
| `agent_complete` | Agent finished |
| `error` | Execution error |
| `cancelled` | User cancelled |

**Debugging Events**:
```bash
# Check Redis connection (inside container)
redis-cli ping

# View Redis stream for a session
redis-cli XREAD STREAMS session:20260125_150542_1c4fce4f 0

# Count persisted events
sqlite3 data/ag3ntum.db "SELECT COUNT(*) FROM events WHERE session_id = '20260125_150542_1c4fce4f';"

# Check for terminal event (did session complete?)
sqlite3 data/ag3ntum.db "SELECT event_type FROM events WHERE session_id = '20260125_150542_1c4fce4f' AND event_type IN ('agent_complete', 'error', 'cancelled');"
```

**SSE Endpoint**: `GET /sessions/{session_id}/events`
- Query param `after=N` to resume from sequence N
- Supports `Last-Event-ID` header for browser reconnection
- Falls back to `/sessions/{session_id}/events/history` for polling

**Sequence Numbers**:
- Positive: Real events (1, 2, 3...)
- `-1`: Heartbeat (keep-alive)
- `9998`: SSE streaming error
- `9999`: Fallback event from SQLite

### Common Diagnostic Scenarios

**Session stuck in "running" status**:
```bash
# Check if agent process is alive
ps aux | grep "session_id"

# Check for stale session in database
sqlite3 data/ag3ntum.db "SELECT status, updated_at FROM sessions WHERE id = 'SESSION_ID';"

# Force cleanup (API restart cleans stale sessions)
./run.sh restart
```

**Events not appearing in UI**:
1. Check Redis connection: `redis-cli ping`
2. Check SQLite has events: `SELECT COUNT(*) FROM events WHERE session_id = '...'`
3. Check browser console for SSE errors
4. Verify JWT token is valid

**Agent execution failing silently**:
```bash
# Check agent.jsonl for SDK errors
tail -50 users/USERNAME/sessions/SESSION_ID/agent.jsonl | grep -i error

# Check backend.log for exceptions
grep -A5 "Exception\|Traceback" logs/backend.log | tail -30
```

---

## Gotchas & Common Confusions

### 1. Native Tools are BLOCKED

**Wrong**: Trying to use `Bash`, `Read`, `Write` directly
**Right**: Use `mcp__ag3ntum__Bash`, `mcp__ag3ntum__Read`, etc.

Native Claude Code tools are disabled in `permissions.yaml` → `tools.disabled`.

### 2. Sandbox Only Applies to Bash

**Confusion**: "Why doesn't PathValidator use Bubblewrap?"

Bubblewrap (`sandbox.py`) only wraps `mcp__ag3ntum__Bash` subprocess execution. Python file tools (`Read`, `Write`, `Edit`) use `Ag3ntumPathValidator` instead - they run in the main Python process.

### 3. UID Security Requires Bubblewrap

UID dropping (`--uid`, `--gid` flags) happens inside Bubblewrap. It doesn't affect Python file tools. The UID security layer is specifically for subprocess isolation.

### 4. Two Event Systems (Redis + SQLite)

- **Redis Pub/Sub**: Real-time delivery (~1ms), ephemeral
- **SQLite**: Permanent storage, replay capability

Events are published to Redis first, then persisted to SQLite. If you're debugging and Redis shows 0 events, that's normal - it's ephemeral. Check SQLite for history.

### 5. Session Files vs Database

Sessions have **dual storage**:
- **Files**: `users/{username}/sessions/{session_id}/` - SDK compatibility
- **SQLite**: `data/ag3ntum.db` → `sessions` table - Fast queries

Both must stay in sync. `SessionService` handles this.

### 6. Skills are Symlinked

Skills in `workspace/.claude/skills/` are symlinks to actual skill directories. The workspace doesn't contain copies - it links to:
- Global: `/skills/.claude/skills/`
- User: `/users/{username}/.claude/skills/`

### 7. Fail-Closed Design

Security components fail-closed:
- If `CommandSecurityFilter` fails to load rules → ALL commands blocked
- If `PathValidator` fails to validate → Operation denied
- If sandbox fails to initialize → Execution blocked

Never catch security exceptions silently.

### 8. Config Changes Need Restart

After editing `config/*.yaml`:
```bash
./run.sh restart  # Reloads configuration
```

For `Dockerfile` or `requirements.txt` changes:
```bash
./run.sh build --no-cache
```

### 9. Prompt Templates Use Jinja2

Files in `prompts/` are Jinja2 templates:
- `{{ variable }}` - Variable substitution
- `{% for item in list %}` - Loops
- `{% include 'module.j2' %}` - Includes

Variables are injected by `ClaudeAgent` during prompt rendering.

### 10. The "ag3ntum" MCP Server

All Ag3ntum tools are registered under a single MCP server named `ag3ntum`. Tool names follow the pattern:
```
mcp__ag3ntum__ToolName
```

This is configured in `tools/ag3ntum/__init__.py`.

### 11. Web Terminal: SSE vs Polling

The frontend uses SSE (Server-Sent Events) by default but falls back to polling:
- SSE preferred → exponential backoff on failure → polling fallback after 3+ failures
- Polling: fetches `/events/history` every 4s
- SSE upgrade attempts continue every 60s from polling mode

**Debugging**: If events aren't appearing, check:
1. Browser console for SSE connection errors
2. Network tab for `/events` endpoint status
3. `ConnectionManager` state in React DevTools

### 12. Web Terminal: API Cache Invalidation

`apiCache.ts` uses TTL-based caching (1 min default, 5 min for skills):
- Cache keys are based on URL + method
- Manual invalidation: `apiCache.invalidate(key)` or `apiCache.invalidateAll()`
- Stale-while-revalidate: serves stale data while refreshing in background

**Gotcha**: After backend changes, frontend may show stale data until cache expires or user refreshes.

### 13. Web Terminal: Event Deduplication

Events have sequence numbers. The frontend deduplicates using a `Set<number>`:
```typescript
if (seenSequences.has(event.sequence)) return; // Skip duplicate
```

**Gotcha**: If you see duplicate messages, check that the backend is assigning unique sequence numbers.

### 14. Web Terminal: CSS Variables

All styling uses CSS variables in `styles.css`. Never use hardcoded colors:
```css
/* Wrong */
color: #7ec8d4;

/* Right */
color: var(--color-cyan);
```

---

## Documentation Index

| Document | Location | Purpose |
|----------|----------|---------|
| Architecture | `docs/current_architecture.md` | System design, component diagrams |
| Security Layers | `docs/layers_of_security_for_filesystem.md` | 6-layer security model details |
| Path Resolver | `docs/sandbox_path_resolver.md` | Sandbox path translation |
| Web Terminal Client | `../DOCUMENTS/TECHNICAL/web_terminal_client.md` | Frontend architecture & design |
| Debugging | `docs/how-to-debug-agent-with-ag3ntum_debug.md` | ag3ntum_debug.py usage |
| Product Overview | `docs/product_management/01_product_overview.md` | Business summary |
| Features | `docs/product_management/features.md` | Complete feature list |

---

## Development Workflow

### For Small Code Changes
```bash
# Edit Python files
./run.sh restart    # Reload code
./run.sh test       # Verify
```

### For Configuration Changes
```bash
# Edit config/*.yaml
./run.sh restart    # Reload config
```

### For Dependency Changes
```bash
# Edit requirements.txt
./run.sh build --no-cache
./run.sh test --all
```

### For Major Refactoring
```bash
./run.sh cleanup
./run.sh build --no-cache
./run.sh test --all
```

---

## Key Patterns

### Unified Task Execution
Both CLI and API use `execute_agent_task()`:
```python
from src.core.task_runner import execute_agent_task
from src.core.schemas import TaskExecutionParams

params = TaskExecutionParams(
    task="Your task",
    working_dir=Path("/path"),
    tracer=ExecutionTracer(),
)
result = await execute_agent_task(params)
```

### Tracer Pattern
Different output modes use different tracers:
- `ExecutionTracer` - Rich CLI with spinners
- `BackendConsoleTracer` - Timestamped logging
- `EventingTracer` - SSE event emission
- `NullTracer` - Silent (testing)

### Session Structure
```
users/{username}/sessions/{session_id}/
├── session_info.json    # Metadata, resume_id
├── agent.jsonl          # Complete SDK log
└── workspace/
    ├── output.yaml      # Agent output
    └── .claude/skills/  # Symlinks to skills
```

---

## Available Python Modules

See `requirements.txt` for the full list. Key packages:
- `anthropic` - Claude API client
- `claude-agent-sdk` - Agent SDK
- `fastapi` - Web framework
- `pydantic` - Data validation
- `sqlalchemy` - Database ORM
- `redis` - Event streaming
- `detect-secrets` - Secrets scanning
- `pypandoc`, `PyMuPDF`, `pillow` - Document processing

**IMPORTANT**: Study `requirements.txt` before implementing new features to use existing APIs rather than reinventing.
