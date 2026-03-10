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
| `ssh/` | `SSHSecurityConfig`, `SSHProfile`, `SSHConnectionPool`, `SSHCommandFilter` | SSH security config loader, connection pooling, command filtering for remote execution |

---

## API (`src/api/`)

`main.py` (app factory, SSHServiceManager lifespan) | `routes/sessions.py` (CRUD, SSE) | `routes/auth.py` (JWT, token revocation, rate limiting) | `routes/files.py` (file explorer) | `routes/health.py` | `routes/ssh_profiles.py` (SSH profile CRUD) | `routes/reseller.py` (reseller API) | `routes/admin.py` (admin API) | `security_middleware.py` (headers, CSP) | `waf_filter.py` (DoS, body-size tracking) | `models.py` (Pydantic) | `ssh_profile_models.py` (SSH Pydantic) | `reseller_models.py` (reseller/admin Pydantic) | `deps.py` (DI)

**Auth endpoints**:
- `POST /auth/token` — login (rate-limited: 5 failed/account/min, 20 failed/IP/min)
- `POST /auth/change-password` — password change
- `POST /auth/connection-token` — short-lived single-use token for SSE auth
- `POST /auth/logout` — server-side token revocation (increments `token_version`)

**Reseller endpoints** (`/api/v1/reseller/*`): User CRUD (role=user only), API key management (create/rotate/revoke + CIDR IP allowlisting), usage stats, WHMCS metrics (`/usage/metrics`), usage export (`/usage/export` JSON/CSV), feature flags, spending limits, settings, skills, webhooks CRUD + test + delivery log
**Admin endpoints** (`/api/v1/admin/*`): Reseller CRUD, platform statistics, audit log (paginated + filterable), platform config mutation (`GET/PUT /config` for features/quotas/spending), data retention (`GET/PUT /retention`, `POST /retention/run`)

---

## Services (`src/services/`)

`agent_runner.py` (background tasks, fires session.completed webhooks) | `session_service.py` (SQLite + files) | `event_service.py` (SSE persistence) | `redis_event_hub.py` (Pub/Sub) | `auth_service.py` (JWT, token versioning) | `user_service.py` (CRUD, shared GID setup) | `mount_service.py` (mount auth, mtime-cached) | `connection_token.py` (short-lived single-use SSE tokens) | `rate_limiter.py` (Redis-based auth rate limiting) | `api_key_service.py` (create/validate/rotate/revoke, CIDR IP allowlisting with IPv6 normalisation, audit logging) | `api_key_rate_limiter.py` (per-key rate limiting) | `reseller_service.py` (reseller CRUD, suspension cascading) | `reseller_quota_service.py` (reseller-level quotas) | `feature_flag_service.py` (3-tier flag resolution, DB-backed platform config with load/update) | `spending_guard.py` (3-tier spending cap enforcement, fires spending alert webhooks) | `usage_service.py` (session usage recording, WHMCS metrics, CSV/JSON export) | `webhook_service.py` (CRUD, HMAC-SHA256 signed delivery, exponential retry) | `webhook_processor.py` (background retry loop, 30s interval) | `data_retention_service.py` (configurable purging of old records) | `retention_processor.py` (background daily purge job) | `ssh_service_manager.py` (singleton SSH infrastructure, per-session context building) | `ssh_profile_service.py` (SSH profile CRUD, vault encryption) | `vault_service.py` (shared vault factory for secret encryption/decryption)

**DB utilities** (`src/db/`): `models.py` (SQLAlchemy, includes Reseller, APIKey, APIKeyAuditLog, UsageRecord, ResellerQuota, UserSkill, ResellerSkillLibrary, PlatformConfig, WebhookEndpoint, WebhookDeliveryLog) | `retry.py` (`with_db_retry` decorator) | `alembic/versions/` (3 migrations: reseller support, platform config, webhook tables)

---

## MCP Tools (`tools/ag3ntum/`) — 14 tools

| Tool | Security | Purpose |
|------|----------|---------|
| `mcp__ag3ntum__Read` | PathValidator | Read local files |
| `mcp__ag3ntum__Write` | PathValidator | Write local files |
| `mcp__ag3ntum__Edit` | PathValidator | Edit local files |
| `mcp__ag3ntum__MultiEdit` | PathValidator | Batch edit local files |
| `mcp__ag3ntum__Bash` | CmdFilter + Bubblewrap + UID | Execute shell commands |
| `mcp__ag3ntum__Glob` | PathValidator | Find local files by pattern |
| `mcp__ag3ntum__Grep` | PathValidator | Search local file contents |
| `mcp__ag3ntum__LS` | PathValidator | List local directories |
| `mcp__ag3ntum__WebFetch` | Domain blocklist | Fetch web content |
| `mcp__ag3ntum__AskUserQuestion` | — | Ask user question |
| `mcp__ag3ntum__ReadDocument` | Size limits | Read agent docs |
| `mcp__ag3ntum__SSHConnect` | SSHSecurityConfig | Connect to SSH host (test connection) |
| `mcp__ag3ntum__SSHExec` | SSHSecurityConfig + SSHCommandFilter | Execute command on SSH host |
| `mcp__ag3ntum__SSHRead` | SSHSecurityConfig | Read file on SSH host |

**Native tools BLOCKED** via `permissions.yaml` → `tools.disabled`. All ops use `mcp__ag3ntum__*`. SSH tools (Connect/Exec/Read) only available if SSH profiles configured.

---

## Production Frontend Server (`src/web_frontend_server.py`)

| File | Class | Purpose |
|------|-------|---------|
| `web_frontend_server.py` | `app` (Starlette) | Serves pre-built React bundle from `/web_dist` with SPA client-side routing fallback. Used in production mode only. |

---

## Docker Compose Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Main compose: api + web (prod static server) + redis |
| `docker-compose.dev.yml` | Overrides web container for Vite dev server (HMR, npm install) |
| `docker-compose.test.yml` | Test overlay (test entrypoint, test volumes) |
| `docker-compose.override.yml` | Auto-generated external mounts |

---

## System Prompts (`prompts/system-prompts/`)

| File | Purpose |
|------|---------|
| `01-identity.md` | Agent identity, model, platform version |
| `02-security-constraints.md` | Security rules, forbidden operations |
| `03-execution.md` | Execution environment, tool availability |
| `04-context-management.md` | Context preferences (resumption, thinking) |
| `05-tool-descriptions.md` | Tool use patterns and examples |
| `06-output-formatting.md` | Output formatting rules |
| `07-human-interaction.md` | Human confirmation patterns |
| `07b-ssh.md` | **SSH remote server access** (conditional on SSH_ENABLED flag) |
| `08-skill-management.md` | Skill creation, update, packaging |
| `09-team-collaboration.md` | Team task management |
| `10-compaction.md` | Message compaction rules |

**Conditional prompts**: `07b-ssh.md` wrapped in `{% if SSH_ENABLED %}` block — only injected if user has SSH profiles configured.

---

## Vite Configuration (`src/web_terminal_client/`)

| File | Purpose |
|------|---------|
| `vite.shared.mjs` | Shared resolve aliases (react, react-dom, etc.) for both build and test |
| `vite.config.mjs` | Vite dev server + production build config (imports shared aliases) |
| `vitest.config.mjs` | Vitest test config (imports shared aliases + test-only aliases) |

---

## Web Terminal (`src/web_terminal_client/`)

React 18.3 + TypeScript 5.6 + Vite 5.4. Full arch: @`../DOCUMENTS/TECHNICAL/web_terminal_client.md`

**Files**: `App.tsx` (orchestrator, decomposed) | `api.ts` (client) | `adminApi.ts` (admin/reseller API client) | `ConnectionManager.ts` (SSE state machine, backoff, polling fallback) | `sse.ts` (thin adapter, delegates to ConnectionManager) | `AuthContext.tsx` (JWT, `isAdmin`/`isReseller` computed props) | `ProtectedRoute.tsx` (role-based route guard) | `hooks/` (7) | `components/messages/` (14) | `components/input/InputField.tsx` | `components/input/StatusFooter.tsx` | `components/dashboard/` (DashboardLayout, StatsCard, DataTable, StatusBadge, ConfirmDialog, SecretDisplay) | `pages/admin/` (AdminDashboard, ResellerList, ResellerDetail) | `pages/reseller/` (ResellerDashboard, UserList, UserDetail, ApiKeyManagement) | `FileExplorer.tsx` | `FileViewer.tsx` | `MarkdownRenderer.tsx` | `types/admin.ts` (admin/reseller TypeScript interfaces) | `styles/` (18 CSS files, split by component + dashboard.css)

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
