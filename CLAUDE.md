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
- **NEVER delete or move `config/*.yaml` files**: Config files are gitignored, instance-specific, and may contain credentials. Deleting them breaks `./run.sh build` and can lose user customizations. To test config validation behavior, use a temporary directory or mock — never touch the real `config/` files.

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
│   ├── prompt-overrides.yaml      # Prompt override allowlist for users
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
│   ├── core/                      # Agent logic (40 files)
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
├── prompts/                       # Prompt templates (${VAR} + Jinja2)
│   ├── system-prompts/            # 10 numbered .md system prompt modules
│   ├── system-reminders/          # 43 .md contextual reminder templates
│   ├── agent-prompts/             # Specialized agent mode prompts
│   ├── modules/                   # Shared .md modules (security, tools, skills, core_principles)
│   ├── subagents/                 # Subagent-specific templates (.md)
│   ├── roles/                     # Role definitions (.md)
│   └── user.md                    # User task prompt template
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

- **Core** (`src/core/`, 40 files) — Agent orchestration, sandbox, security, prompts, tracers
- **API** (`src/api/`) — FastAPI app, routes (sessions, auth, files, health), middleware, WAF
- **Services** (`src/services/`, 18 files) — Session, event, auth, user, mount, Redis pub/sub
- **MCP Tools** (`tools/ag3ntum/`, 11 tools) — Sandboxed replacements for native tools (Read/Write/Edit/Bash/Glob/Grep/LS/WebFetch/AskUserQuestion/ReadDocument/MultiEdit)
- **Web Terminal** (`src/web_terminal_client/`) — React 18.3 + TypeScript 5.6 + Vite 5.4

→ See [docs/source-code-map.md](docs/source-code-map.md) for file-level class/purpose tables.

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
| 6 | Prompts | `prompts/modules/security.md`, `prompts/system-prompts/02-security-constraints.md` | LLM |

- **Fail-closed**: Security load/validate failure → operation denied. Never catch silently.
- **Read-only source**: `src/` volume mounted `:ro` — agents cannot modify application code.
- **Native tools BLOCKED** via `permissions.yaml` → `tools.disabled`. All ops use `mcp__ag3ntum__*`.

→ See [docs/security-overview.md](docs/security-overview.md) for seccomp, UID isolation, shared GID, WAF, secrets scanning details.

---

## Testing

**Always write tests alongside new feature implementations** — do not wait to be asked. If modifying existing functionality, update related tests in the same pass.

| Suite | Location | Runner |
|-------|----------|--------|
| Backend | `tests/backend/` (35) | pytest |
| Redis | `tests/backend/redis/` (3) | pytest |
| Core | `tests/core-tests/` | pytest |
| Security | `tests/security/` (5) | pytest |
| Sandbox | `tests/sandbox/` | pytest |
| E2E | `tests/backend/test_zzz_e2e_server.py` | pytest |
| Frontend | `tests/web_terminal_console/` (20+) | vitest |

**Markers**: `unit`, `integration`, `slow`, `e2e`. `asyncio_mode = auto`.
`@pytest.mark.e2e` / `@pytest.mark.slow` skipped by default. `./run.sh test` passes `--run-e2e`. `--quick` skips them.

→ See [docs/testing-guide.md](docs/testing-guide.md) for writing backend/E2E/frontend tests, pre-built users, fixtures.
→ See [docs/sse-schema-validation.md](docs/sse-schema-validation.md) for SSE schema test workflow.

---

## Documentation

**Always update relevant documentation alongside new feature implementations, fixes or refactoring** — do not wait to be asked. If modifying existing functionality, update related documentation in the same pass. Scan @DOCUMENTS/TECHNICAL or @doc folders for the document to update.

---

## Configuration

**CRITICAL**: Never delete, move, or overwrite `config/*.yaml` files. They are gitignored, instance-specific, and may contain credentials. Use temp dirs or mocks for testing.

```yaml
# config/agent.yaml
default_model: claude-sonnet-4-20250514
max_turns: 100
timeout_seconds: 1800
role: default                     # from prompts/roles/
```

```yaml
# config/secrets.yaml
ANTHROPIC_API_KEY: "sk-ant-..."
sandboxed_envs:               # Per-user, sandbox-only
  OPENAI_API_KEY: "sk-..."
```

→ See [docs/configuration.md](docs/configuration.md) for validation tiers, auto-provisioning, user config, external mounts.

---

## Key Patterns

**Unified execution**: CLI + API → `execute_agent_task(TaskExecutionParams(...))`

**Tracers** (`src/core/tracers/`): `ExecutionTracer` (CLI) | `BackendConsoleTracer` (log) | `EventingTracer` (SSE) | `NullTracer` (test) | `QuietTracer` (minimal output)

**Task queue**: Redis-backed, priority scoring. Quotas: 4 global, 2/user, 50/day. Auto-resumes on restart. @`task_queue_and_auto_resume.md`

**Session storage**: Files (`users/{user}/sessions/{id}/agent.jsonl` + `workspace/`) + SQLite (`sessions` table). `SessionService` syncs both.

**Events**: Agent → Redis (real-time, ephemeral) → SSE | Agent → SQLite (persistent) → polling fallback

**Prompts**: Unified template system via `PromptTemplateEngine`. All prompts use `.md` format with `${VAR}` syntax and Jinja2-compatible directives (`{% include %}`, `{% if %}`, `{# comment #}`). Main system prompts in `prompts/system-prompts/` are auto-loaded alphabetically. Shared modules (`.md` in `prompts/modules/`) are included by subagent templates. `PromptManager` handles loading, caching, and user overrides. Core operating principles (`03-core-principles.md` + `modules/core_principles.md`) are injected into both main agent and all subagents.

**MCP server**: Single `ag3ntum` server → `mcp__ag3ntum__ToolName`. Registered in `tools/ag3ntum/__init__.py`.

**Circuit breaker**: `CircuitBreaker` (extracted to `circuit_breaker.py`) tracks consecutive identical tool failures. After 5 failures with same error signature, trips and stops agent with FAILED status. `PatternDetector` (extracted to `pattern_detector.py`) detects unproductive loops. Both used by `TraceProcessor`.

---

## Diagnostics & Troubleshooting

| Log | Content |
|-----|---------|
| `logs/backend.log` | API server (10MB rotation, 5 backups) |
| `logs/agent_cli.log` | CLI execution |
| `logs/latest-test-results.log` | Last test run (overwritten) |

→ See [docs/troubleshooting.md](docs/troubleshooting.md) for DB queries, debug script, troubleshooting flowcharts.

---

## Versioning & Releases

**Version source of truth**: `VERSION` file in project root (plain-text semver, e.g. `0.1.0`).

**Branch model**: `main` (active development) | `release` (stable, protected)

**Release process** (see `docs/plans/release-workflow.md`):
1. Update `VERSION` + `CHANGELOG.md`
2. PR to `release` → GitHub Actions gate checks
3. On merge → auto-creates git tag `vX.Y.Z` + GitHub Release

**Where version is used**: Health endpoint (`/api/v1/health`), Docker image labels/tags, `APP_VERSION` env var.

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
21. **Unified prompt template engine** — `PromptTemplateEngine` handles `${VAR}` syntax and Jinja2-compatible directives (`{% include %}`, `{% if %}`, `{# comment #}`). Include resolution is recursive with circular-include protection (max depth 5). All prompts (system prompts, modules, subagent templates) use `.md` format and are processed by the same engine. `base_dir` for include resolution is set to `PROMPTS_DIR` by `PromptManager`.
22. **Prompt overrides are allowlisted** — Users can customize prompts only for files listed in `config/prompt-overrides.yaml`. Security prompts (02-security-constraints.md) and system reminders are NOT overridable. User overrides go in `users/{username}/.prompts/`.
23. **TodoWrite/TodoRead excluded from turn count** — `TraceProcessor` no longer counts TodoWrite/TodoRead tool calls as agent turns (they are planning tools). Tracked separately via `todo_tool_count`.
24. **Agent self-assessment for session status** — `determine_session_status()` in `agent_core.py` reads structured `request_status` headers from agent output. Agent's own status (COMPLETE/PARTIAL/FAILED) is primary; defaults to COMPLETE if no header found.
25. **Security refusals show "failed" status** — When the agent correctly refuses a malicious/disallowed request, session status is "failed" because the agent self-assesses as unable to complete the task. This is expected behavior. To distinguish security refusals from actual failures, check the agent's response content for refusal language. A dedicated "refused" status is a future enhancement.
26. **NEVER delete `config/*.yaml` files** — They are gitignored, instance-specific, and may contain credentials. `run.sh` auto-provisions safe configs from `.example` templates on build, but `secrets.yaml` requires manual creation. Deleting configs during development/testing/debugging is forbidden — use temp dirs or mocks instead.

**Study `requirements.txt` before new features** — use existing packages.
