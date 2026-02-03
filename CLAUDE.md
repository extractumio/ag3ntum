# CLAUDE.md - Ag3ntum Reference Guide

Primary reference for Claude Code working with this repository. Consult `../DOCUMENTS/TECHNICAL/` for deep-dive architecture docs.

## Quick Reference

```bash
./run.sh build              # Build image + start containers
./run.sh restart            # Restart containers (code/config changes)
./run.sh test               # Run ALL tests (backend + security + sandbox + UI)
./run.sh test --quick       # Skip E2E/slow tests
./run.sh test --backend     # Backend only (with E2E)
./run.sh test --security    # Security tests only
./run.sh test --sandboxing  # Sandbox tests only
./run.sh test --e2e         # E2E tests only
./run.sh test --ui          # Frontend vitest only (alias: --frontend)
./run.sh test --subset "session*,auth*"  # Pattern-matched test files
./run.sh shell              # Shell into API container
./run.sh cleanup            # Stop + remove containers/images/networks
./run.sh rebuild            # cleanup + build (full reset)
./run.sh create-user        # Create user account
./run.sh delete-user        # Delete user account
./run.sh cleanup-test-users # Remove test users
```

**URLs** (after `./run.sh build`): Web UI http://localhost:50080 | API http://localhost:40080

**Identity**: Python 3.13+ | AGPL-3.0 | claude-agent-sdk 0.1.23 | 6-layer defense-in-depth | Ubuntu 24.04 container

---

## Project Structure

```
Project/
├── config/                     # All configuration
│   ├── agent.yaml              # Model, max_turns, timeout, role
│   ├── api.yaml                # Host, port, CORS, Redis URL
│   ├── secrets.yaml            # ANTHROPIC_API_KEY, sandboxed_envs
│   ├── subagents.yaml          # Subagent definitions
│   ├── llm-api-proxy.yaml      # Custom LLM proxy routing
│   ├── external-mounts.yaml    # Host folder access for agents
│   ├── user_requirements.txt   # User-installable pip packages
│   ├── redis.conf              # Redis server config
│   ├── security/               # Security configs (7 files)
│   │   ├── permissions.yaml         # Tool enablement, sandbox settings
│   │   ├── tools-security.yaml      # PathValidator, secrets scanning
│   │   ├── command-filtering.yaml   # 140+ regex patterns (16 categories)
│   │   ├── upload-filtering.yaml    # Upload MIME/extension filters
│   │   ├── sensitive-data-scanner.yaml # Secrets detection patterns
│   │   ├── seccomp-isolated.json    # Seccomp (UID 50000-60000)
│   │   └── seccomp-direct.json      # Seccomp (direct UID mode)
│   └── test/sudoers-test       # Test-only elevated sudoers
├── src/
│   ├── core/                   # Core agent logic (32 files)
│   ├── api/                    # FastAPI application
│   ├── services/               # Business logic (16 files)
│   ├── security/               # Secrets scanner
│   ├── db/                     # SQLAlchemy models + DB setup
│   ├── cli/                    # User management CLI tools
│   ├── config.py               # Configuration loader
│   └── web_terminal_client/    # React 18 + TypeScript + Vite frontend
├── tools/ag3ntum/              # 11 custom MCP tools
├── prompts/                    # Jinja2 prompt templates
├── tests/                      # All test suites
│   ├── backend/                # API, services, routes (28 test files)
│   │   └── redis/              # Redis-specific tests (3 files)
│   ├── core-tests/             # Core component tests
│   ├── security/               # Command filtering, UID, user isolation
│   ├── sandbox/                # Bubblewrap sandbox tests
│   └── web_terminal_console/   # React vitest tests (20+ files)
├── scripts/                    # CLI helpers (debug, security check)
├── skills/                     # Skill definitions (symlinked to sessions)
├── docs/plans/                 # Future design plans
├── deploy/                     # Deployment scripts
├── data/                       # SQLite DB, auto-generated manifests
├── logs/                       # Runtime + test logs
├── users/                      # Per-user session data
├── docker-compose.yml          # Main: api + web + redis services
├── docker-compose.test.yml     # Test overlay (root, sudoers, workers)
├── docker-compose.override.yml # Auto-generated mount volumes
├── Dockerfile                  # Ubuntu 24.04, bubblewrap, node, python
├── run.sh                      # Main CLI (~1700 lines)
└── install.sh                  # One-command installer (curl-friendly)
```

---

## Platform Setup

### Linux (Ubuntu)
- Requires sudo for `chown` of writable dirs to UID 45045 (container user)
- `run.sh` auto-detects and prompts for sudo password
- Uses Linux-specific `stat -c '%u'` for ownership checks

### macOS
- Docker Desktop handles all permissions — no sudo needed
- `run.sh` skips all `chown` operations automatically
- Bash 3 compatible (macOS ships Bash 3)

### First-Time Setup
```bash
# Option A: One-command installer
curl -fsSL https://raw.githubusercontent.com/extractumio/ag3ntum/main/install.sh | bash

# Option B: Manual
git clone <repo> && cd Project
cp config/agent.yaml.example config/agent.yaml
cp config/api.yaml.example config/api.yaml
cp config/secrets.yaml.example config/secrets.yaml
# Edit config/secrets.yaml → set ANTHROPIC_API_KEY
./run.sh build
./run.sh create-user
```

### Docker Recreation

| Change Type | Command |
|-------------|---------|
| Code changes only | `./run.sh restart` |
| Config YAML changes | `./run.sh restart` |
| Dockerfile / requirements.txt | `./run.sh build --no-cache` |
| Add/remove external mounts | `./run.sh build` |
| Full reset | `./run.sh rebuild` (cleanup + build) |

**Docker services**: `ag3ntum-api` (uvicorn), `ag3ntum-web` (vite dev), `redis` (7-alpine)

**Container specifics**: Capabilities SYS_ADMIN/SETUID/SETGID/CHOWN, seccomp/apparmor unconfined. CPU-specific numpy/pandas installed based on SSE4.2 detection. ARM64 supported (rollup binary auto-detected).

---

## Source Code Index

### Core (`src/core/`) — 32 files

| File | Key Class/Function | Purpose |
|------|-------------------|---------|
| `agent_core.py` | `ClaudeAgent` | Main agent orchestrator, SDK integration |
| `task_runner.py` | `execute_agent_task()` | **Unified entry point** for CLI + API |
| `schemas.py` | `TaskExecutionParams` | Execution parameters dataclass |
| `permission_profiles.py` | `PermissionManager` | Tool access control, session context |
| `sessions.py` | `SessionManager` | File-based session CRUD, workspace symlinks |
| `sandbox.py` | `SandboxExecutor` | Bubblewrap sandbox + UID dropping |
| `uid_security.py` | `UIDSecurityConfig` | UID/GID validation, seccomp generation |
| `path_validator.py` | `Ag3ntumPathValidator` | File path validation for tools |
| `command_security.py` | `CommandSecurityFilter` | Regex-based command blocking |
| `tracer.py` | `TracerBase`, `ExecutionTracer` | Output tracing (CLI/API/SSE/Null) |
| `trace_processor.py` | `TraceProcessor` | SDK message → event processing |

### API (`src/api/`)

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app factory, middleware |
| `routes/sessions.py` | Session CRUD, task execution, SSE |
| `routes/auth.py` | JWT authentication |
| `routes/files.py` | File explorer endpoints |
| `routes/health.py` | Health check, config |
| `security_middleware.py` | HTTP headers, CSP, host validation |
| `waf_filter.py` | Request size limits, DoS prevention |
| `models.py` | Pydantic request/response models |
| `deps.py` | Dependency injection (JWT, DB) |

### Services (`src/services/`) — 16 files

| File | Purpose |
|------|---------|
| `agent_runner.py` | Background task execution |
| `session_service.py` | Session lifecycle (SQLite + files) |
| `event_service.py` | SSE event persistence |
| `redis_event_hub.py` | Redis Pub/Sub for real-time events |
| `auth_service.py` | JWT authentication |
| `user_service.py` | User CRUD |
| `mount_service.py` | External mount authorization (mtime-cached) |

### MCP Tools (`tools/ag3ntum/`) — 11 tools

| Tool | Security Layer | Replaces |
|------|---------------|----------|
| `mcp__ag3ntum__Read` | PathValidator | Read |
| `mcp__ag3ntum__Write` | PathValidator | Write |
| `mcp__ag3ntum__Edit` | PathValidator | Edit |
| `mcp__ag3ntum__MultiEdit` | PathValidator | MultiEdit |
| `mcp__ag3ntum__Bash` | CommandFilter + Bubblewrap + UID | Bash |
| `mcp__ag3ntum__Glob` | PathValidator | Glob |
| `mcp__ag3ntum__Grep` | PathValidator | Grep |
| `mcp__ag3ntum__LS` | PathValidator | LS |
| `mcp__ag3ntum__WebFetch` | Domain blocklist | WebFetch |
| `mcp__ag3ntum__AskUserQuestion` | — | AskUserQuestion |
| `mcp__ag3ntum__ReadDocument` | Size limits | *New* |

**Native Claude Code tools are BLOCKED** via `permissions.yaml` → `tools.disabled`. All operations go through `mcp__ag3ntum__*`.

### Web Terminal Client (`src/web_terminal_client/`)

React 18.3 + TypeScript 5.6 + Vite 5.4. See `../DOCUMENTS/TECHNICAL/web_terminal_client.md` for full architecture.

**Key files**: `App.tsx` (main orchestrator), `api.ts` (API client), `sse.ts` (SSE + polling fallback), `ConnectionManager.ts` (state machine), `AuthContext.tsx` (JWT), `hooks/` (6 custom hooks), `components/messages/` (14 rendering files), `FileExplorer.tsx`, `FileViewer.tsx`, `MarkdownRenderer.tsx`, `styles.css` (CSS variables, dark theme)

**Hooks**: `useSSEConnection`, `useSessionManager`, `useUIState`, `useFileOperations`

**Connection flow**: `connected` → `reconnecting` → `polling` → `degraded`

**SSE events**: `agent_start`, `tool_start`, `tool_complete`, `message`, `thinking`, `subagent_*`, `agent_complete`, `error`, `cancelled`

**Frontend dev** (runs inside Docker via `ag3ntum-web` container):
```bash
# Tests run in Docker container:
./run.sh test --ui          # Vitest: build check + test suite

# Or manually inside container:
docker exec -it project-ag3ntum-web-1 npm run test:run
docker exec -it project-ag3ntum-web-1 npm run build
```

**CSS rule**: Always use `var(--color-*)` variables from `styles.css`, never hardcoded colors.

**Cache**: `apiCache.ts` — TTL-based (1 min default, 5 min skills), stale-while-revalidate. Backend changes may show stale until cache expires.

---

## Security Architecture (6-Layer Defense-in-Depth)

See `../DOCUMENTS/TECHNICAL/layers_of_security_for_filesystem.md` for comprehensive details.

| Layer | Component | Files | Scope |
|-------|-----------|-------|-------|
| **0** | Inbound WAF | `api/waf_filter.py` | API requests |
| **1** | Docker | `docker-compose.yml` | Container isolation |
| **2** | Bubblewrap + UID | `core/sandbox.py`, `core/uid_security.py` | Bash subprocess only |
| **3** | Ag3ntum Tools | `tools/ag3ntum/*`, `core/path_validator.py` | File/command ops |
| **4** | Command Filter | `core/command_security.py` | Bash commands |
| **5** | Security Middleware | `api/security_middleware.py` | HTTP responses |
| **6** | Prompts | `prompts/modules/security.j2` | LLM guidance |

**UID isolation** (Layer 2): Each user gets unique UID (50000-60000 range, ISOLATED mode). OS-enforced via bubblewrap `--uid`/`--gid`. See `../DOCUMENTS/TECHNICAL/sandbox_path_resolver.md` for path translation.

**Fail-closed design**: If any security component fails to load/validate → operation denied. Never catch security exceptions silently.

**Secrets scanning**: `src/security/sensitive_data_scanner.py` + `config/security/sensitive-data-scanner.yaml` — auto-redacts API keys, tokens, passwords in File Explorer.

---

## Testing Guide

### Test Architecture

All tests run **inside Docker** via `docker-compose.test.yml` overlay (injects test sudoers, runs as root then drops to ag3ntum_api, `AG3NTUM_TEST_MODE=true`).

| Suite | Location | Runner | What it tests |
|-------|----------|--------|---------------|
| Backend | `tests/backend/` (28 files) | pytest | API, services, routes, models |
| Redis | `tests/backend/redis/` (3 files) | pytest | EventHub, streaming, SSE E2E |
| Core | `tests/core-tests/` | pytest | Agent core components |
| Security | `tests/security/` (5 files) | pytest | Command filtering, UID, isolation |
| Sandbox | `tests/sandbox/` | pytest | Bubblewrap execution |
| E2E | `tests/backend/test_zzz_e2e_server.py` | pytest | Full server integration |
| Frontend | `tests/web_terminal_console/` (20+ files) | vitest | React components, hooks, API |

### Test Markers and E2E

```ini
# tests/backend/pytest.ini
markers = unit, integration, slow, e2e
asyncio_mode = auto
```

Tests marked `@pytest.mark.e2e` or `@pytest.mark.slow` are **skipped by default**. `./run.sh test` passes `--run-e2e` to include them. `./run.sh test --quick` skips them.

### Writing New Tests

**Backend unit test pattern**:
```python
# tests/backend/test_<module>.py
import pytest
from tests.backend.conftest import *  # fixtures auto-discovered

class TestMyFeature:
    @pytest.mark.unit
    async def test_basic_behavior(self, test_app, auth_headers):
        """Uses in-memory SQLite, mock agent runner from conftest."""
        response = await test_app.get("/endpoint", headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.e2e
    async def test_full_flow(self, test_app):
        """Skipped unless --run-e2e passed."""
        ...
```

**Key conftest fixtures** (`tests/backend/conftest.py`):
- `db_engine` / `db_session` — in-memory SQLite
- `test_app` — FastAPI test client with all dependencies
- `auth_headers` — valid JWT headers
- `mock_agent_runner` — mock for agent execution
- `temp_session_dir` — temporary session directory
- `test_user_manager` — creates/cleans test users

**Redis test fixtures** (`tests/backend/redis/conftest.py`):
- `redis_connection` — real Redis connection
- `event_hub` — `RedisEventHub` instance
- `tracer_factory` — creates `EventingTracer` instances

**Frontend test pattern** (vitest + React Testing Library + MSW):
```typescript
// tests/web_terminal_console/unit/<Component>.test.tsx
import { renderWithProviders } from '../utils/renderWithProviders';
import { screen } from '@testing-library/react';

test('renders component', () => {
  renderWithProviders(<MyComponent />);
  expect(screen.getByText('expected')).toBeInTheDocument();
});
```

Frontend test setup: `tests/web_terminal_console/setup.ts` (MSW server, jest-dom matchers, window mocks). Mock handlers in `tests/web_terminal_console/mocks/`.

### Test Results

Output saved to `logs/latest-test-results.log` (overwritten each run):
```bash
cat logs/latest-test-results.log
grep -A 10 "FAILED\|ERROR" logs/latest-test-results.log
```

---

## Configuration

### Agent (`config/agent.yaml`)
```yaml
model: claude-sonnet-4-20250514  # Model ID
max_turns: 100                    # Max conversation turns
timeout_seconds: 1800             # Global timeout
role: default                     # Role from prompts/roles/
```

### User Configuration
- `config/user_requirements.txt` — pip packages users can install in sandbox
- `config/user_secrets.yaml.example` — per-user encrypted credentials template
- `config/subagents.yaml` — subagent definitions (models, tools, prompts)
- `config/llm-api-proxy.yaml` — route to custom LLM endpoints (see `../DOCUMENTS/TECHNICAL/how-to-connect-custom-llm.md`)

### User Management
```bash
./run.sh create-user              # Interactive user creation
./run.sh delete-user              # Delete user
./run.sh cleanup-test-users       # Remove test-prefixed users
```
Users stored in `data/ag3ntum.db` → `users` table with `linux_uid` for sandbox isolation.

### External Mounts (`config/external-mounts.yaml`)

Two-part system: Docker volumes (build-time) + symlink authorization (session-level). See `../DOCUMENTS/TECHNICAL/external_mounts.md`.

| Change | Command |
|--------|---------|
| Add/remove/change mount path | `./run.sh build` |
| Change user authorization list | New session only |

### Secrets (`config/secrets.yaml`)
```yaml
ANTHROPIC_API_KEY: "sk-ant-..."
sandboxed_envs:               # Per-user, visible only in sandbox
  OPENAI_API_KEY: "sk-..."
```

---

## Key Patterns

### Unified Task Execution
Both CLI and API use `execute_agent_task()`:
```python
from src.core.task_runner import execute_agent_task
from src.core.schemas import TaskExecutionParams
result = await execute_agent_task(TaskExecutionParams(
    task="Your task", working_dir=Path("/path"), tracer=tracer))
```

### Tracer Pattern
`ExecutionTracer` (CLI spinners) | `BackendConsoleTracer` (logging) | `EventingTracer` (SSE) | `NullTracer` (testing)

### Task Queue + Auto-Resume
Redis-backed priority queue with quotas (4 global, 2 per-user, 50 daily). Auto-resumes `running`/`queued` sessions on restart. See `../DOCUMENTS/TECHNICAL/task_queue_and_auto_resume.md`.

### Session Dual Storage
- **Files**: `users/{username}/sessions/{id}/` — `agent.jsonl` (SDK log), `workspace/` (output + mounts)
- **SQLite**: `data/ag3ntum.db` → `sessions` table — status, cost, turns, checkpoints

### Event System (Redis + SQLite)
```
Agent → Redis Stream (real-time, ephemeral) → SSE to Browser
     ↘ SQLite events table (persistent)    ↗ Polling fallback
```

---

## Diagnostics

### Logs
| File | Content |
|------|---------|
| `logs/backend.log` | API server (10MB rotation, 5 backups) |
| `logs/agent_cli.log` | CLI execution |
| `logs/latest-test-results.log` | Last test run (overwritten) |

```bash
docker logs project-ag3ntum-api-1 --tail 100 -f     # Container logs
./run.sh shell && tail -f /logs/backend.log          # Inside container
grep -i "denied\|blocked" logs/backend.log           # Security denials
```

**Loggers**: `src.api`, `src.services`, `src.core`, `src.db`, `ag3ntum`, `tools.ag3ntum`, `uvicorn`, `fastapi`

### Database
```bash
sqlite3 data/ag3ntum.db
# Sessions: SELECT id, status, task, total_cost_usd FROM sessions ORDER BY created_at DESC LIMIT 10;
# Users:    SELECT username, linux_uid FROM users;
# Events:   SELECT COUNT(*) FROM events WHERE session_id = '...';
```

Tables: `users`, `sessions`, `events`, `tokens`

### Debug Agent Execution
```bash
./venv/bin/python scripts/ag3ntum_debug.py -r "task" --user "email" --password "pass"
# Flags: -v (verbose) | -s (security only) | -d (dump session)
```
See `../DOCUMENTS/TECHNICAL/how-to-debug-agent-with-ag3ntum_debug.md`.

### Common Issues

**Session stuck running**: `./run.sh restart` (cleans stale sessions)

**Events missing in UI**: Check Redis (`redis-cli ping`), SQLite events, browser console SSE errors, JWT validity

**Agent failing silently**: Check `users/USER/sessions/ID/agent.jsonl` and `logs/backend.log` for exceptions

---

## Gotchas

1. **Native tools BLOCKED** — Use `mcp__ag3ntum__*` only. Configured in `permissions.yaml` → `tools.disabled`.

2. **Bubblewrap = Bash only** — File tools (Read/Write/Edit) use `Ag3ntumPathValidator` in-process. Only `mcp__ag3ntum__Bash` runs in bubblewrap sandbox with UID dropping.

3. **Two event systems** — Redis (real-time, ephemeral) + SQLite (persistent, replay). Check SQLite for history, not Redis.

4. **Session dual storage** — Files (SDK compat) + SQLite (queries). `SessionService` keeps them in sync.

5. **Skills are symlinked** — `workspace/.claude/skills/` → global `/skills/` + user `/users/{username}/`.

6. **Config changes need restart** — `./run.sh restart` for YAML. `./run.sh build` for Dockerfile/requirements/mounts.

7. **Prompts are Jinja2** — `{{ var }}`, `{% for %}`, `{% include %}`. Variables injected by `ClaudeAgent`.

8. **MCP server pattern** — All tools under single `ag3ntum` MCP server: `mcp__ag3ntum__ToolName`. Registered in `tools/ag3ntum/__init__.py`.

9. **Frontend SSE fallback** — SSE → exponential backoff → polling after 3+ failures → SSE upgrade retry every 60s.

10. **Event deduplication** — Frontend uses `Set<number>` on sequence numbers. Duplicate messages = check backend sequence assignment.

11. **Mount gotcha** — Both global AND per-user mounts need `./run.sh build` when adding new mounts. Only user authorization list is dynamic.

---

## Documentation Cross-References

All deep-dive docs live in `../DOCUMENTS/TECHNICAL/`:

| Document | Key Topics |
|----------|------------|
| `current_architecture.md` | System design, component diagrams, execution flow |
| `layers_of_security_for_filesystem.md` | 6-layer model detail, session hardening, skills propagation |
| `sandbox_path_resolver.md` | Path translation between bwrap/Docker/API contexts |
| `external_mounts.md` | Mount lifecycle, two-part system, rebuild requirements |
| `web_terminal_client.md` | React architecture, SSE, hooks, components |
| `how-to-debug-agent-with-ag3ntum_debug.md` | Debug tool usage, flags, artifact locations |
| `how-to-connect-custom-llm.md` | LLM API proxy setup for local/custom models |
| `task_queue_and_auto_resume.md` | Queue architecture, priority, quotas, recovery |
| `current_sse.md` | SSE implementation, Redis streaming, sequence numbers |
| `current_event_hooks_callbacks.md` | Event hook system, callback registration |
| `sandboxed_environment_variables.md` | Per-user env var injection in sandbox |
| `dynamic_mounts_security_analysis.md` | Security analysis of mount system |
| `inbound_waf_filter.md` | WAF filter rules, request validation |
| `ask-user-question-logic.md` | Human-in-the-loop interaction flow |

Design plans in `docs/plans/`: PostgreSQL migration, host command bridge, prompt system migration, mountpoint redesign.

**Study `requirements.txt` before implementing new features** — use existing packages rather than reinventing.
