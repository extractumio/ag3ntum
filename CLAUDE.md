# Ag3ntum Reference

## Architecture & Design Docs (located `../DOCUMENTS/TECHNICAL/`)

Consult these before fixing bugs or designing features:

| Document | What it covers |
|----------|---------------|
| `current_architecture.md` | **Start here.** System design, dual entry points (CLI + Web), execution flow, component diagrams |
| `layers_of_security_for_filesystem.md` | 6-layer defense model, Docker/bwrap/PathValidator/CmdFilter/middleware/prompts, session hardening |
| `sandbox_path_resolver.md` | Path translation between bubblewrap, Docker, and API contexts |
| `web_terminal_client.md` | React frontend architecture, SSE streaming, hooks, component hierarchy |
| `current_sse.md` | SSE implementation, Redis streaming, sequence numbers, reconnection |
| `current_event_hooks_callbacks.md` | Event hook system, tracer callbacks, lifecycle events |
| `task_queue_and_auto_resume.md` | Redis priority queue, quotas (4 global/2 user/50 daily), auto-resume on restart |
| `external_mounts_guide.md` | **Mount reference guide**: types, use cases, restart rules, path display |
| `dynamic_mounts_security_analysis.md` | Security analysis of mount system, attack surface |
| `how-to-connect-custom-llm.md` | LLM API proxy setup for local/custom models (llama.cpp, Ollama, etc.) |
| `how-to-debug-agent-with-ag3ntum_debug.md` | Debug script flags, artifact locations, auth vs filesystem usernames |
| `sandboxed_environment_variables.md` | Per-user env var injection in bubblewrap sandbox |
| `inbound_waf_filter.md` | WAF rules, request size limits, DoS prevention |
| `ask-user-question-logic.md` | Human-in-the-loop interaction flow, AskUserQuestion tool |

Design plans in `docs/plans/`: Place all the new design and implementation documents here.

---

## Environment Constraints

- **Sudo access via Interactive Bash**: If the `interactive-bash` MCP tool is available, sudo commands (including `./run.sh rebuild`, `./run.sh build`, and other operations requiring sudo) can be executed autonomously without asking the user for confirmation. Use `mcp__interactive-bash__interactive_start` to launch the command, then `mcp__interactive-bash__interactive_send` to provide the password when prompted. If the interactive-bash tool is NOT available, do not suggest solutions requiring sudo — inform the user and let them handle it.
- **Tests do NOT require sudo**: `./run.sh test` (and all its flags) runs without sudo. Do not prefix test commands with sudo.
- **Container UID is 45045**: When fixing file permissions or ownership in the container environment, do not set ownership to the host username. Use the container UID.
- **Use absolute paths**: Always use absolute paths in shell scripts, not relative paths.
- **Test user UIDs**: Avoid UIDs in the 50000-50100 range as they conflict with real database users. Use UIDs 59990+ for test users.

---

## Code Editing

When editing files, prefer the Write or Edit tool over bash sed commands. sed-based edits have repeatedly corrupted files (especially App.tsx and shell scripts), requiring git restore.

---

## Commands

```bash
./run.sh build                         # Build image, start containers
./run.sh restart                       # Restart (code/config changes)
./run.sh cleanup                       # Stop, remove containers/images
./run.sh rebuild                       # cleanup → build (full reset)
./run.sh shell                         # Shell into API container
./run.sh create-user                   # Create user account
./run.sh delete-user                   # Delete user account
./run.sh cleanup-test-users            # Remove test users
./run.sh test                          # All tests EXCEPT E2E (default)
./run.sh test --all                    # ALL tests including E2E
./run.sh test --quick                  # Skip E2E/slow
./run.sh test --backend                # Backend only (no E2E)
./run.sh test --security               # Security only
./run.sh test --sandboxing             # Sandbox only
./run.sh test --only-e2e               # E2E only
./run.sh test --e2e                    # Alias for --only-e2e
./run.sh test --ui                     # Frontend vitest (alias: --frontend)
./run.sh test --subset "session*,auth*" # Pattern-match test files
```

**Worktree commands** (multi-instance support):
```bash
./worktree.sh create <branch>         # Create worktree with isolated Docker stack
./worktree.sh create <branch> --name N --slot S  # Explicit name and port slot
./worktree.sh list                     # List all instances with ports and status
./worktree.sh status <name>            # Detailed status of an instance
./worktree.sh destroy <name>           # Stop Docker stack and remove worktree
```

**Claude Code command**: `/create_worktree <branch> [--build]` — agentic worktree creation

**Always use `./run.sh`** for building, testing, and running containers. Do not use raw docker/docker-compose commands unless explicitly asked.

URLs after build: **Web UI** http://localhost:50080 | **API** http://localhost:40080

Python 3.13+ | AGPL-3.0 | claude-agent-sdk 0.1.23 | Ubuntu 24.04 container

---

## Structure

```
Project/
├── config/
│   ├── agent.yaml                 # Model, max_turns, timeout, role
│   ├── api.yaml                   # Host, port, CORS, Redis URL
│   ├── secrets.yaml               # ANTHROPIC_API_KEY, sandboxed_envs
│   ├── subagents.yaml             # Subagent definitions
│   ├── llm-api-proxy.yaml         # Custom LLM proxy routing
│   ├── external-mounts.yaml       # Host folder access for agents
│   ├── user_requirements.txt      # User-installable pip packages
│   ├── redis.conf
│   ├── security/                  # 8 files
│   │   ├── permissions.yaml       # Tool enablement, sandbox
│   │   ├── tools-security.yaml    # PathValidator, secrets scanning
│   │   ├── command-filtering.yaml # 140+ regex (16 categories)
│   │   ├── upload-filtering.yaml  # MIME/extension filters
│   │   ├── sensitive-data-scanner.yaml
│   │   ├── seccomp-container.json # Container-level seccomp profile
│   │   ├── seccomp-isolated.json  # UID 50000-60000
│   │   └── seccomp-direct.json
│   └── test/sudoers-test          # Test-only sudoers
├── src/
│   ├── core/                      # Agent logic (35 files)
│   │   └── tracers/               # 7 files: base, cli, backend, eventing, null, quiet
│   ├── api/                       # FastAPI
│   ├── services/                  # Business logic (18 files)
│   ├── security/                  # Secrets scanner
│   ├── db/                        # SQLAlchemy models + retry.py
│   ├── cli/                       # User management CLI
│   ├── config.py
│   └── web_terminal_client/       # React 18 + TS + Vite
│       └── src/styles/            # 17 component-scoped CSS files
├── tools/ag3ntum/                 # 11 MCP tools
├── prompts/                       # Jinja2 templates
├── tests/
│   ├── backend/ (29 files)        # API, services, routes
│   │   └── redis/ (3 files)       # EventHub, streaming
│   ├── core-tests/                # Agent core
│   ├── security/ (5 files)        # Cmd filter, UID, isolation
│   ├── sandbox/                   # Bubblewrap
│   └── web_terminal_console/ (20+)# React vitest + MSW
├── scripts/                       # Debug, security check, sync_linux_users.py
├── skills/                        # Symlinked to sessions
├── data/                          # SQLite DB, manifests
├── logs/                          # Runtime + test logs
├── users/                         # Per-user sessions
├── docker-compose.yml             # api + web + redis
├── docker-compose.test.yml        # Test overlay
├── docker-compose.override.yml    # Auto-generated mounts
├── Dockerfile                     # Ubuntu 24.04
├── entrypoint-api.sh              # API: calls sync_linux_users.py → setpriv drop
├── entrypoint-test.sh             # Test: sudoers + sync_linux_users.py + test users → setpriv drop
├── entrypoint-web.sh              # Web: npm install → setpriv drop
├── run.sh                         # CLI (~1700 lines)
├── worktree.sh                    # Multi-instance worktree manager
└── install.sh                     # One-command installer
```

---

## Platform Setup

**Linux**: Requires sudo for `chown` to UID 45045. `run.sh` auto-detects, prompts.
**macOS**: Docker Desktop handles permissions. No sudo. Bash 3 compatible.

### First-Time Setup
```bash
# One-command:
curl -fsSL https://raw.githubusercontent.com/extractumio/ag3ntum/main/install.sh | bash

# Manual:
git clone <repo> && cd Project
cp config/{agent,api,secrets}.yaml.example config/{agent,api,secrets}.yaml  # then edit secrets.yaml
./run.sh build && ./run.sh create-user
```

### When to Rebuild

| Change | Command |
|--------|---------|
| Code / config YAML | `./run.sh restart` |
| Dockerfile / requirements.txt | `./run.sh build --no-cache` |
| External mounts (add/remove/path) | `./run.sh build` |
| Full reset | `./run.sh rebuild` |

Services: `ag3ntum-api` (uvicorn) + `ag3ntum-web` (vite) + `redis` (7-alpine)
Capabilities: SYS_ADMIN, SETUID, SETGID, CHOWN. CPU-specific numpy/pandas (SSE4.2 detection). ARM64 supported.

---

## Source Code

### Core (`src/core/`)

| File | Class | Purpose |
|------|-------|---------|
| `agent_core.py` | `ClaudeAgent` | Agent orchestrator, SDK integration, LLM proxy routing |
| `checkpoint_tracker.py` | `CheckpointTracker` | File checkpoint tracking during execution |
| `task_runner.py` | `execute_agent_task()` | **Unified entry** for CLI + API |
| `schemas.py` | `TaskExecutionParams` | Execution params dataclass |
| `permission_profiles.py` | `PermissionManager` | Tool access, session context |
| `sessions.py` | `SessionManager` | Session CRUD, workspace symlinks, file ownership |
| `sandbox.py` | `SandboxExecutor` | Bubblewrap + UID dropping |
| `uid_security.py` | `UIDSecurityConfig` | UID/GID validation, seccomp |
| `path_validator.py` | `Ag3ntumPathValidator` | File path validation, session UID registry, `docker_to_display_path()` for tool output |
| `command_security.py` | `CommandSecurityFilter` | Regex command blocking |
| `circuit_breaker.py` | `CircuitBreaker` | Extracted from trace_processor; consecutive failure detection |
| `pattern_detector.py` | `PatternDetector` | Extracted from trace_processor; unproductive loop detection |
| `tracer.py` | — | Thin re-export shim; actual implementations in `tracers/` package |
| `tracers/` | `TracerBase`, `ExecutionTracer`, `BackendConsoleTracer`, `EventingTracer`, `NullTracer`, `QuietTracer` | Tracer implementations (7 files) |
| `trace_processor.py` | `TraceProcessor` | SDK message → events (delegates to circuit_breaker/pattern_detector) |

### API (`src/api/`)

`main.py` (app factory) | `routes/sessions.py` (CRUD, SSE) | `routes/auth.py` (JWT, token revocation, rate limiting) | `routes/files.py` (file explorer) | `routes/health.py` | `security_middleware.py` (headers, CSP) | `waf_filter.py` (DoS, body-size tracking) | `models.py` (Pydantic) | `deps.py` (DI)

**Auth endpoints**:
- `POST /auth/token` — login (rate-limited: 5 failed/account/min, 20 failed/IP/min)
- `POST /auth/change-password` — password change
- `POST /auth/connection-token` — short-lived single-use token for SSE auth
- `POST /auth/logout` — server-side token revocation (increments `token_version`)

### Services (`src/services/`)

`agent_runner.py` (background tasks) | `session_service.py` (SQLite + files) | `event_service.py` (SSE persistence) | `redis_event_hub.py` (Pub/Sub) | `auth_service.py` (JWT, token versioning) | `user_service.py` (CRUD, shared GID setup) | `mount_service.py` (mount auth, mtime-cached) | `connection_token.py` (short-lived single-use SSE tokens) | `rate_limiter.py` (Redis-based auth rate limiting)

**DB utilities** (`src/db/`): `models.py` (SQLAlchemy) | `retry.py` (`with_db_retry` decorator, extracted from event/session services)

### MCP Tools (`tools/ag3ntum/`) — 11 tools

| Tool | Security | Replaces |
|------|----------|----------|
| `mcp__ag3ntum__Read` | PathValidator | Read |
| `mcp__ag3ntum__Write` | PathValidator | Write |
| `mcp__ag3ntum__Edit` | PathValidator | Edit |
| `mcp__ag3ntum__MultiEdit` | PathValidator | MultiEdit |
| `mcp__ag3ntum__Bash` | CmdFilter + Bubblewrap + UID | Bash |
| `mcp__ag3ntum__Glob` | PathValidator | Glob |
| `mcp__ag3ntum__Grep` | PathValidator | Grep |
| `mcp__ag3ntum__LS` | PathValidator | LS |
| `mcp__ag3ntum__WebFetch` | Domain blocklist | WebFetch |
| `mcp__ag3ntum__AskUserQuestion` | — | AskUserQuestion |
| `mcp__ag3ntum__ReadDocument` | Size limits | *New* |

**Native tools BLOCKED** via `permissions.yaml` → `tools.disabled`. All ops use `mcp__ag3ntum__*`.

### Web Terminal (`src/web_terminal_client/`)

React 18.3 + TypeScript 5.6 + Vite 5.4. Full arch: @`../DOCUMENTS/TECHNICAL/web_terminal_client.md`

**Files**: `App.tsx` (orchestrator, decomposed) | `api.ts` (client) | `ConnectionManager.ts` (SSE state machine, backoff, polling fallback) | `sse.ts` (thin adapter, delegates to ConnectionManager) | `AuthContext.tsx` (JWT) | `hooks/` (7) | `components/messages/` (14) | `components/input/InputField.tsx` | `components/input/StatusFooter.tsx` | `FileExplorer.tsx` | `FileViewer.tsx` | `MarkdownRenderer.tsx` | `styles/` (17 CSS files, split by component)

**Hooks**: `useSSEConnection` | `useSessionManager` | `useUIState` | `useFileOperations` | `useConversation`

**Connection**: `connected` → `reconnecting` → `polling` → `degraded`

**SSE events**: `agent_start` | `tool_start` | `tool_complete` | `message` | `thinking` | `subagent_*` | `agent_complete` | `error` | `cancelled`

**CSS**: Always `var(--color-*)`, never hardcoded colors. Monolithic `styles.css` replaced by `styles/` directory with 17 component-scoped CSS files (variables, base, layout, messages, panels, login, file-explorer, etc.). Entry point: `styles/index.css`.

**Cache**: `apiCache.ts` — TTL 1 min (5 min skills), stale-while-revalidate.

**Frontend tests** (Docker):
```bash
./run.sh test --ui                                  # Build check + vitest
docker exec -it project-ag3ntum-web-1 npm run test:run  # Manual
```

---

## Security (6-Layer)

Read @`../DOCUMENTS/TECHNICAL/layers_of_security_for_filesystem.md`

| Layer | Component | Files | Scope |
|-------|-----------|-------|-------|
| 0 | WAF | `api/waf_filter.py` | API requests |
| 1 | Docker | `docker-compose.yml` | Container |
| 2 | Bubblewrap + UID | `core/sandbox.py`, `core/uid_security.py` | Bash only |
| 3 | Ag3ntum Tools | `tools/ag3ntum/*`, `core/path_validator.py` | File/cmd ops |
| 4 | Command Filter | `core/command_security.py` | Bash cmds |
| 5 | Middleware | `api/security_middleware.py` | HTTP |
| 6 | Prompts | `prompts/modules/security.j2` | LLM |

- **Container seccomp**: `seccomp-container.json` applied at container level (replaces previous `seccomp:unconfined`). Per-session seccomp profiles (`seccomp-isolated.json`, `seccomp-direct.json`) layered on top.
- **UID isolation**: Each user → unique UID (50000..60000, ISOLATED mode). OS-enforced via bwrap. `UIDSecurityConfig.__post_init__` validates absolute bounds (50000 min for isolated, 1000 min for direct). Path translation: @`sandbox_path_resolver.md`
- **Shared GID model**: `ag3ntum_api` added to each sandbox user's primary group at creation. Session files use 660/770 (no world access). Cross-user isolation by PathValidator.
- **File ownership**: Write/Edit/MultiEdit tools `chown` files to sandbox user immediately. Session dirs `chown`'d at creation. `ensure_secure_session_files()` re-applies 660/770 post-execution.
- **Read-only source**: `src/` volume mounted read-only (`:ro`) in `docker-compose.yml` to prevent agent modification of application code.
- **WAF hardening**: Body-size-tracking wrapper on `request._receive` prevents Content-Length spoofing bypass of size limits.
- **Auth rate limiting**: Redis-based rate limiting on login (5 failed/account/min, 20 failed/IP/min). Fails open if Redis unavailable.
- **Token revocation**: `token_version` field on User model. Logout increments version, invalidating all outstanding JWTs server-side.
- **Fail-closed**: Security load/validate failure → operation denied. Never catch silently.
- **Secrets scanning**: `src/security/sensitive_data_scanner.py` + `sensitive-data-scanner.yaml` → auto-redacts in File Explorer

---

## Testing

**Always write tests alongside new feature implementations** — do not wait to be asked. If modifying existing functionality, update related tests in the same pass.

All tests run **inside Docker** via `docker-compose.test.yml` (root → drops to ag3ntum_api via `setpriv --init-groups`, `AG3NTUM_TEST_MODE=true`).

**Test entrypoint** (`entrypoint-test.sh`): Installs test sudoers, syncs Linux users from DB, creates fully-equipped test users (`ag3ntum_tester_a` UID 59990, `ag3ntum_tester_b` UID 59991) with DB entries, venvs, persistent storage, and shared GID memberships, then drops privileges. Test users are at the high end of the isolated range to avoid conflicts with real users. Credentials: email `ag3ntum_tester_a@test.local`, password `TestPassword123!`.

| Suite | Location | Runner |
|-------|----------|--------|
| Backend | `tests/backend/` (28) | pytest |
| Redis | `tests/backend/redis/` (3) | pytest |
| Core | `tests/core-tests/` | pytest |
| Security | `tests/security/` (5) | pytest |
| Sandbox | `tests/sandbox/` | pytest |
| E2E | `tests/backend/test_zzz_e2e_server.py` | pytest |
| Frontend | `tests/web_terminal_console/` (20+) | vitest |

**Markers**: `unit`, `integration`, `slow`, `e2e`. `asyncio_mode = auto`.
`@pytest.mark.e2e` / `@pytest.mark.slow` skipped by default. `./run.sh test` passes `--run-e2e`. `--quick` skips them.

### Writing Backend Tests

```python
# tests/backend/test_<module>.py
class TestFeature:
    @pytest.mark.unit
    async def test_behavior(self, test_app, auth_headers):
        response = await test_app.get("/endpoint", headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.e2e
    async def test_full_flow(self, test_app): ...
```

**Backend fixtures** (`conftest.py`): `db_engine`/`db_session` (in-memory SQLite) | `test_app` (FastAPI client) | `auth_headers` (JWT) | `mock_agent_runner` | `temp_session_dir` | `test_user_manager`

**Redis fixtures** (`redis/conftest.py`): `redis_connection` | `event_hub` | `tracer_factory`

### Writing E2E / Functional Tests (Real Users)

Tests that need real Linux users (sandbox execution, filesystem permissions, mount access, user isolation) **must reuse pre-built test users**, not create them dynamically.

**Why**: The API process gets its supplementary groups at startup via `setpriv --init-groups`. Dynamically-created users add `ag3ntum_api` to the new user's group, but the already-running API process doesn't pick up the change. This causes `Permission denied` on session workspace directories. Restarting the container mid-test is not viable.

**Pre-built test users** (created by `entrypoint-test.sh`):

| Field | tester_a | tester_b |
|-------|----------|----------|
| Username | `ag3ntum_tester_a` | `ag3ntum_tester_b` |
| UID/GID | 59990 | 59991 |
| Email | `ag3ntum_tester_a@test.local` | `ag3ntum_tester_b@test.local` |
| Password | `TestPassword123!` | `TestPassword123!` |
| Home | `/users/ag3ntum_tester_a` | `/users/ag3ntum_tester_b` |

Both have: Linux accounts, DB entries, Python venvs, persistent storage, shared GID memberships, `.claude/skills/` dirs.

**Pattern for E2E tests**:
```python
from types import SimpleNamespace

# Constants (reuse across test files)
PREBUILT_USER_A_USERNAME = "ag3ntum_tester_a"
PREBUILT_USER_A_UID = 59990

def _prebuilt_user(username: str, uid: int) -> SimpleNamespace:
    return SimpleNamespace(username=username, linux_uid=uid)

# Fixture — no DB session needed
@pytest.fixture
def test_user(self) -> SimpleNamespace:
    return _prebuilt_user(PREBUILT_USER_A_USERNAME, PREBUILT_USER_A_UID)

# For API auth, login with known credentials
response = await client.post("/auth/token", json={
    "email": "ag3ntum_tester_a@test.local",
    "password": "TestPassword123!",
})
```

**Rules**:
- Prefix test artifacts with `_test_` or `_e2e_` for easy cleanup
- Always clean up test files in fixture teardown (pre-built users persist across runs)
- Use `try/finally` for cleanup in test bodies that create files
- Only `TestRealUserCreation` in `test_real_user_integration.py` creates users dynamically (it tests the creation flow itself)
- For two-user isolation tests, use both `ag3ntum_tester_a` and `ag3ntum_tester_b`

### Writing Frontend Tests

vitest + React Testing Library + MSW:
```typescript
// tests/web_terminal_console/unit/<Component>.test.tsx
import { renderWithProviders } from '../utils/renderWithProviders';
test('renders', () => {
  renderWithProviders(<MyComponent />);
  expect(screen.getByText('expected')).toBeInTheDocument();
});
```
Setup: `setup.ts` (MSW, jest-dom, window mocks). Mocks: `mocks/handlers.ts`.

### Test Output

`logs/latest-test-results.log` (overwritten each run):
```bash
grep -A 10 "FAILED\|ERROR" logs/latest-test-results.log
```

### SSE Schema Validation

Anthropic's SSE streaming format is used in two contexts:
1. **Direct API calls** — `TraceProcessor` parses events from Claude Agent SDK
2. **LLM Proxy** — Translator produces Anthropic-format events from OpenAI responses

When Anthropic changes the SSE format (new event types, new fields, changed structure), both contexts break. Schema validation tests detect these changes early.

**Files**:
- `src/api/llm_proxy/schemas.py` — Pydantic models for all SSE event types (shared by both contexts)
- `tests/backend/test_sse_schemas.py` — 59 tests validating schemas
- `tests/backend/fixtures/anthropic_sse_samples.json` — Recorded real API events
- `scripts/record_sse_samples.py` — Re-records fixtures from live API

**What breaks when Anthropic changes format**:
| Component | Location | Impact |
|-----------|----------|--------|
| TraceProcessor | `src/core/trace_processor.py` | Fails to parse new event types, misses usage stats, wrong status |
| LLM Proxy Translator | `src/api/llm_proxy/translator.py` | Produces invalid events, SDK rejects responses |
| Event Persistence | `src/services/event_service.py` | New fields not stored, lost in polling fallback |

**Test categories and what failures indicate**:

| Test Class | Failure Indicates |
|------------|-------------------|
| `TestEnums` | New/renamed stop reasons, content types, or delta types |
| `TestContentBlocks` | Changed structure of text/tool_use/thinking blocks |
| `TestDeltas` | Changed structure of text_delta/input_json_delta/thinking_delta |
| `TestUsage` | New usage fields (tokens, caching, service tier) |
| `TestSSEEvents` | Changed event payload structure |
| `TestSSEParsing` | Changed SSE wire format (event:/data: lines) |
| `TestSSEStreamValidation` | Changed event ordering requirements |
| `TestToolUseStreamOrder` | Changed tool input streaming protocol |
| `TestTranslatorOutput` | Our translator produces invalid events |
| `TestRecordedAPIEvents` | Real API format differs from schemas |
| `TestTraceProcessorEventCoverage` | TraceProcessor missing handler for new event/delta type |

**Workflow when Claude Code updates and tests fail**:

```bash
# 1. Run tests to see what broke
./run.sh test --subset "sse_schemas"

# 2. Re-record fixtures from live API
python3 scripts/record_sse_samples.py

# 3. Run tests again — new failures show schema drift
./run.sh test --subset "sse_schemas"

# 4. Update schemas.py to match new API format
# 5. Update translator.py if LLM proxy output format changed
# 6. Update trace_processor.py if new event types need handling
# 7. Run tests until green
```

**Recording script usage**:
```bash
# Record from API (uses ANTHROPIC_API_KEY from env or secrets.yaml)
python3 scripts/record_sse_samples.py

# Preview only (no API calls)
python3 scripts/record_sse_samples.py --dry-run

# Use specific model
python3 scripts/record_sse_samples.py --model claude-sonnet-4-20250514
```

The script makes 3 API calls: text-only, single tool call, multiple tool calls. Output saved to `tests/backend/fixtures/anthropic_sse_samples.json`.

**Known API fields** (discovered via recording):
- `ping` event: keepalive during long streams
- `cache_creation`: nested object with `ephemeral_5m_input_tokens`, `ephemeral_1h_input_tokens`
- `service_tier`, `inference_geo`: in usage object
- `input_json_delta`: first delta can be empty string

---

## Documentation

**Always update relevant documentation alongside new feature implementations, fixes or refactoring** — do not wait to be asked. If modifying existing functionality, update related documentation in the same pass. Scan @DOCUMENTS/TECHNICAL or @doc folders for the document to update.

---

## Configuration

### Agent (`config/agent.yaml`)
```yaml
default_model: claude-sonnet-4-20250514
max_turns: 100
timeout_seconds: 1800
role: default                     # from prompts/roles/
```

### User Config
- `user_requirements.txt` — pip packages for sandbox
- `user_secrets.yaml` — per-user encrypted credentials
- `subagents.yaml` — subagent models, tools, prompts
- `llm-api-proxy.yaml` — custom LLM routing, **auto-routes** models via proxy (@`how-to-connect-custom-llm.md`)

### Users
`./run.sh create-user` / `delete-user` / `cleanup-test-users`
Stored: `data/ag3ntum.db` → `users` table, `linux_uid` for sandbox isolation.

### External Mounts
Two-part: Docker volumes (build-time) + symlink auth (session-level). Read @`external_mounts.md`.

| Change | Command |
|--------|---------|
| Add/remove/change path | `./run.sh build` |
| Change user auth list | New session only |

### Secrets (`config/secrets.yaml`)
```yaml
ANTHROPIC_API_KEY: "sk-ant-..."
sandboxed_envs:               # Per-user, sandbox-only
  OPENAI_API_KEY: "sk-..."
```

---

## Key Patterns

**Unified execution**: CLI + API → `execute_agent_task(TaskExecutionParams(...))`

**Tracers** (`src/core/tracers/`): `ExecutionTracer` (CLI) | `BackendConsoleTracer` (log) | `EventingTracer` (SSE) | `NullTracer` (test) | `QuietTracer` (minimal output)

**Task queue**: Redis-backed, priority scoring. Quotas: 4 global, 2/user, 50/day. Auto-resumes on restart. @`task_queue_and_auto_resume.md`

**Session storage**: Files (`users/{user}/sessions/{id}/agent.jsonl` + `workspace/`) + SQLite (`sessions` table). `SessionService` syncs both.

**Events**: Agent → Redis (real-time, ephemeral) → SSE | Agent → SQLite (persistent) → polling fallback

**Prompts**: Jinja2 templates in `prompts/`. `{{ var }}`, `{% for %}`, `{% include %}`. Injected by `ClaudeAgent`.

**MCP server**: Single `ag3ntum` server → `mcp__ag3ntum__ToolName`. Registered in `tools/ag3ntum/__init__.py`.

**Circuit breaker**: `CircuitBreaker` (extracted to `circuit_breaker.py`) tracks consecutive identical tool failures. After 5 failures with same error signature, trips and stops agent with FAILED status. `PatternDetector` (extracted to `pattern_detector.py`) detects unproductive loops. Both used by `TraceProcessor`.

---

## Diagnostics & Troubleshooting

### Logs

| Log | Content |
|-----|---------|
| `logs/backend.log` | API server (10MB rotation, 5 backups) |
| `logs/agent_cli.log` | CLI execution |
| `logs/latest-test-results.log` | Last test run (overwritten) |

```bash
docker logs project-ag3ntum-api-1 --tail 100 -f     # Container stdout
./run.sh shell && tail -f /logs/backend.log          # Inside container
grep -i "denied\|blocked" logs/backend.log           # Security denials
grep "ERROR\|Exception" logs/backend.log             # Errors
```

**Loggers**: `src.api` | `src.services` | `src.core` | `src.db` | `ag3ntum` | `tools.ag3ntum` | `uvicorn` | `fastapi`

### Database

`sqlite3 data/ag3ntum.db` — tables: `users`, `sessions`, `events`, `tokens`

```sql
-- List recent sessions
SELECT id, status, task, total_cost_usd FROM sessions ORDER BY created_at DESC LIMIT 10;
-- Check user UIDs (sandbox debug)
SELECT username, linux_uid FROM users WHERE linux_uid BETWEEN 50000 AND 60000;
-- Count events for session
SELECT COUNT(*) FROM events WHERE session_id = 'SESSION_ID';
-- Find terminal event
SELECT event_type FROM events WHERE session_id = 'SESSION_ID'
  AND event_type IN ('agent_complete', 'error', 'cancelled');
```

### Debug Agent Execution

```bash
./venv/bin/python scripts/ag3ntum_debug.py -r "task" --user "email" --password "pass"
# -v  verbose (all events)
# -s  security only (blocked ops)
# -d  dump session files
# -m/--model  override model (e.g., "openrouter:openai/gpt-5.2")
```
Read @`how-to-debug-agent-with-ag3ntum_debug.md`. Note: auth uses email, filesystem uses username.

### Troubleshooting

**Session stuck in "running"**:
1. Check process: `ps aux | grep session_id` inside container
2. Check DB: `SELECT status, updated_at FROM sessions WHERE id = '...';`
3. Fix: `./run.sh restart` — cleans stale sessions on startup

**Events not appearing in UI**:
1. Redis alive? `redis-cli ping` (inside container)
2. Events persisted? `SELECT COUNT(*) FROM events WHERE session_id = '...';`
3. Browser console → SSE connection errors?
4. JWT token valid? Check expiry in browser DevTools.

**Agent failing silently**:
1. Check SDK log: `tail -50 users/USER/sessions/ID/agent.jsonl | grep -i error`
2. Check backend: `grep -A5 "Exception\|Traceback" logs/backend.log | tail -30`

**Container won't start**:
1. Port conflict: `lsof -i :40080` / `lsof -i :50080`
2. Stale containers: `./run.sh cleanup && ./run.sh build`
3. Permission issue (Linux): `./run.sh build` re-runs chown

**Tests failing unexpectedly**:
1. Check `logs/latest-test-results.log` for full output
2. Stale container? `./run.sh rebuild && ./run.sh test`
3. Redis down? Tests need Redis: `docker ps | grep redis`
4. Wrong platform binaries (UI tests)? `run.sh` auto-detects and reinstalls node_modules

**SSE streaming broken**:
1. Frontend falls back: SSE → backoff → polling (3+ fails) → SSE retry (60s)
2. Check `ConnectionManager` state in React DevTools
3. Check `/sessions/{id}/events` endpoint in Network tab
4. Fallback endpoint: `/sessions/{id}/events/history` (polling)

---

## Gotchas

1. **Native tools BLOCKED** — `mcp__ag3ntum__*` only (`permissions.yaml` → `tools.disabled`)
2. **Bubblewrap = Bash only** — File tools use PathValidator in-process; only Bash runs in bwrap with UID drop
3. **Two event systems** — Redis (ephemeral) + SQLite (persistent). Check SQLite for history.
4. **Session dual storage** — Files (SDK) + SQLite (queries). `SessionService` syncs.
5. **Skills symlinked** — `workspace/.claude/skills/` → `/skills/` + `/users/{user}/`
6. **Config → restart** — YAML: `restart`. Dockerfile/deps/mounts: `build`.
7. **Mounts need build** — Both global AND per-user mounts need `./run.sh build`. Only user auth list is dynamic.
8. **Frontend SSE fallback** — SSE → backoff → polling (3+ fails) → SSE retry (60s)
9. **Event dedup** — `Set<number>` on sequence. Duplicates = check backend sequence assignment.
10. **Shared GID for file access** — `ag3ntum_api` is in each sandbox user's group. Session files are 660/770 (owner+group). Write/Edit tools chown to sandbox user. Adding users requires `./run.sh build` (Dockerfile sudoers rule).
11. **Entrypoints sync Linux users** — Container `/etc/passwd` is ephemeral. `entrypoint-api.sh` and `entrypoint-test.sh` call `scripts/sync_linux_users.py` to recreate accounts from DB on every start. Test entrypoint also creates fully-equipped `ag3ntum_tester_a` (59990) and `ag3ntum_tester_b` (59991) with DB entries, venvs, and shared GID memberships.
12. **Supplementary groups are set at process start** — `setpriv --init-groups` reads `/etc/group` once when the API process launches. Dynamically adding users (and their groups) after startup does NOT update the running process's group list. Tests that need real user directories must use pre-built test users, not dynamic `UserService.create_user()`.
13. **Always use `./run.sh test <flags>`** — Never run tests via raw `docker exec` or manual `docker compose exec`. The `run.sh` CLI handles: (a) starting the container with `docker-compose.test.yml` overlay (test entrypoint, test volumes), (b) running as `ag3ntum_api` user (not root), (c) restoring production mode after tests. Running `docker exec` directly runs as root, which causes false test results (e.g., security tests that check UID dropping will fail).
14. **Container recreation for entrypoint changes** — `docker compose up -d` reuses existing containers if the image hasn't changed. After modifying `entrypoint-test.sh`, use `docker compose up -d --force-recreate ag3ntum-api` or `./run.sh rebuild` to ensure the new entrypoint runs.
15. **Test user UIDs at high end of range** — Pre-built test users use UIDs 59990/59991 (top of 50000–60000 isolated range). Dynamic users allocated sequentially from 50000. Always check `getent passwd` or `SELECT linux_uid FROM users` before assigning UIDs to avoid collisions with existing users.
16. **LLM proxy auto-routing** — Models in `llm-api-proxy.yaml` are automatically routed via the internal proxy (`/api/llm-proxy`). The SDK's `ANTHROPIC_BASE_URL` is set dynamically. API keys can be in env vars OR `secrets.yaml` → `sandboxed_envs`.
17. **Token revocation is server-side** — Logout now increments `token_version` on the User model, invalidating all outstanding JWTs. Not just client-side cookie clearing.
18. **Auth rate limiting is Redis-based** — Login rate limits (5 failed/account/min, 20 failed/IP/min) stored in Redis. Fails open if Redis is unavailable (allows login rather than locking users out).
19. **Tool `_*_impl()` functions** — MCP tools (bash/edit/glob/grep/ls/read) have extracted `_*_impl()` functions for testability without MCP wrapper. Test these directly instead of going through MCP.
20. **`src/` is read-only in container** — Mounted with `:ro` flag. Agents cannot modify application source code at runtime.

**Study `requirements.txt` before new features** — use existing packages.
