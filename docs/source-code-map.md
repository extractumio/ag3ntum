# Source Code Map

Detailed file/class/purpose tables for all subsystems.

---

## Core (`src/core/`)

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
| `prompt_engine.py` | `PromptTemplateEngine`, `PromptContext` | ${VAR} syntax template engine (replaces Jinja2 for main prompts) |
| `prompt_context.py` | `build_prompt_context()` | Context builder with tool names, env vars, flags, security strings |
| `prompt_manager.py` | `PromptManager` | Singleton prompt loader, caching, user overrides, hot reload |
| `system_reminders.py` | `ReminderType`, `get_reminder()` | 42 contextual reminders injected during agent conversations |
| `structured_output.py` | `parse_structured_output()` | Parse structured response headers from agent output |
| `trace_processor.py` | `TraceProcessor` | SDK message → events (delegates to circuit_breaker/pattern_detector) |

---

## API (`src/api/`)

`main.py` (app factory) | `routes/sessions.py` (CRUD, SSE) | `routes/auth.py` (JWT, token revocation, rate limiting) | `routes/files.py` (file explorer) | `routes/health.py` | `security_middleware.py` (headers, CSP) | `waf_filter.py` (DoS, body-size tracking) | `models.py` (Pydantic) | `deps.py` (DI)

**Auth endpoints**:
- `POST /auth/token` — login (rate-limited: 5 failed/account/min, 20 failed/IP/min)
- `POST /auth/change-password` — password change
- `POST /auth/connection-token` — short-lived single-use token for SSE auth
- `POST /auth/logout` — server-side token revocation (increments `token_version`)

---

## Services (`src/services/`)

`agent_runner.py` (background tasks) | `session_service.py` (SQLite + files) | `event_service.py` (SSE persistence) | `redis_event_hub.py` (Pub/Sub) | `auth_service.py` (JWT, token versioning) | `user_service.py` (CRUD, shared GID setup) | `mount_service.py` (mount auth, mtime-cached) | `connection_token.py` (short-lived single-use SSE tokens) | `rate_limiter.py` (Redis-based auth rate limiting)

**DB utilities** (`src/db/`): `models.py` (SQLAlchemy) | `retry.py` (`with_db_retry` decorator, extracted from event/session services)

---

## MCP Tools (`tools/ag3ntum/`) — 11 tools

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

---

## Web Terminal (`src/web_terminal_client/`)

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
