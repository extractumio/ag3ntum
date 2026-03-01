# Ag3ntum Reference

## MANDATORY: Self-Improvement Protocol

This is a standing order. Follow it every session.

### On Failure
After ANY session failure, error, or unexpected behavior:
1. Read this file and relevant docs — identify root cause
2. Missing instructions → add a Gotcha (1-2 lines, cause + prevention)
3. Unclear instructions → improve the wording
4. Referenced doc needs update → update it directly
5. Commit: `docs: CLAUDE.md self-improvement — [description]`

### Periodic Maintenance
- Remove obsolete gotchas, merge duplicates, fix broken links
- Keep this file under 250 lines — extract verbose content to `docs/`
- Verify all `→ See` references resolve to existing files

### Quality Gates
- Each gotcha: max 2 lines, cause AND prevention
- No duplicate facts — single source of truth per fact
- No vague instructions ("be careful with X") — state what to do
- Every feature development shall be followed by /simplify skill to unbload the updated code, improve code quality and readability

---

## Architecture & Design Docs (`../DOCUMENTS/TECHNICAL/`)

Consult before fixing bugs or designing features:

| Document | Covers |
|----------|--------|
| `current_architecture.md` | **Start here.** System design, dual entry points, execution flow |
| `layers_of_security_for_filesystem.md` | 6-layer defense, Docker/bwrap/PathValidator/CmdFilter/middleware/prompts |
| `sandbox_path_resolver.md` | Path translation: bubblewrap ↔ Docker ↔ API |
| `web_terminal_client.md` | React frontend, SSE streaming, hooks, components |
| `current_sse.md` | SSE, Redis streaming, sequence numbers, reconnection |
| `current_event_hooks_callbacks.md` | Event hooks, tracer callbacks, lifecycle events |
| `task_queue_and_auto_resume.md` | Redis priority queue, quotas, auto-resume |
| `external_mounts_guide.md` | Mount types, use cases, restart rules, path display |
| `how-to-connect-custom-llm.md` | LLM API proxy for local/custom models |
| `how-to-debug-agent-with-ag3ntum_debug.md` | Debug script, artifact locations, auth vs filesystem users |
| `sandboxed_environment_variables.md` | Per-user env vars in bubblewrap sandbox |
| `inbound_waf_filter.md` | WAF rules, request size limits, DoS prevention |
| `ask-user-question-logic.md` | Human-in-the-loop AskUserQuestion flow |

Design plans: `docs/plans/`

---

## Agent Rules

- **Use Write/Edit tools** — never bash sed/awk. sed has corrupted files (App.tsx, shell scripts) requiring git restore.
- **Update docs with code** — when implementing features or fixes, update relevant docs in `../DOCUMENTS/TECHNICAL/` or `docs/` in the same pass.
- **Test discipline** — (1) New features: write tests covering the new behavior. (2) Changed code: update existing tests to match, remove tests for deleted behavior. (3) After any test changes: review nearby tests for redundancy, staleness, or overlap — refactor or remove. Do not leave dead tests.
- **Lint after every file change** — Python: `flake8 <file> --config=.flake8`. TypeScript/React: `cd src/web_terminal_client && npx eslint <file>` + `npx tsc --noEmit`. Fix errors before moving on.
- **Run `./run.sh lint` before every commit** — not optional. Run it, fix failures, then commit. Full suite: flake8, bandit, mypy, ESLint, tsc, structural tests.
- **Structural tests are guardrails** — if `tests/structural/` fails, read the error message — it explains the fix.
- **Security errors are not retryable** — if a tool returns a security/permission error, do not retry the same operation. Diagnose the cause.
- **Verify containers before testing** — before `./run.sh test` or `shell`, confirm containers are up with `docker compose ps`.
- **Study `requirements.txt`** before adding dependencies — use existing packages, do not add redundant ones.
- **Empty mounts are valid** — a configured external mount pointing to an empty directory is not an error. Report "no files found".
- **Follow task management flow** — use Plane for all task tracking. Transition states as you work. Use worktrees for all changes. Never merge PRs — only humans merge.

→ See [docs/internals/ag3ntum-task-management-and-flow.md](docs/internals/ag3ntum-task-management-and-flow.md) for full workflow: task states, branching, testing requirements, commit conventions, Definition of Done.

---

## Environment Constraints

- **Sudo via Interactive Bash**: Use `mcp__interactive-bash__interactive_start` + `interactive_send` for sudo commands. If tool unavailable, inform user.
- **Tests do NOT require sudo**: `./run.sh test` runs without sudo. Never prefix with sudo.
- **Container UID is 45045**: Use this UID for file permissions/ownership, not host username.
- **Use absolute paths**: Always in shell scripts, not relative paths.
- **Test user UIDs**: Use 59990+ for test users. Avoid 50000-50100 (real database users).
- **NEVER delete `config/*.yaml`**: Gitignored, instance-specific, may contain credentials. Use temp dirs or mocks for testing.

---

## Commands

```bash
./run.sh setup                         # First run: creates .venv/, installs all dev tools + pre-commit
./run.sh build [--dev]                 # Build + start (--dev for Vite HMR)
./run.sh restart                       # Restart (code/config changes)
./run.sh cleanup | rebuild             # Stop+remove | cleanup→build
./run.sh shell                         # Shell into API container
./run.sh create-user | delete-user     # User management
./run.sh test                          # All tests EXCEPT E2E
./run.sh test --all | --quick          # Include E2E | skip E2E+slow
./run.sh test --backend | --security   # Backend only | security only
./run.sh test --core | --sandboxing    # Core agent only | sandbox only
./run.sh test --e2e                    # E2E only (needs real API key)
./run.sh test --ui                     # Frontend vitest
./run.sh lint                          # flake8 + bandit + mypy + eslint + tsc + structural tests
./run.sh audit                         # pip-audit dependency vulnerability scan
```

**New developer?** Run `./run.sh setup` then `./run.sh build`. That's it.

**Always use `./run.sh`** — never raw docker/docker-compose unless explicitly asked.

→ See [docs/commands-reference.md](docs/commands-reference.md) for worktree commands, rebuild rules, deployment modes.

---

## Key Paths

| Path | Purpose |
|------|---------|
| `src/core/` (40 files) | Agent orchestration, sandbox, security, prompts, tracers |
| `src/api/` | FastAPI routes (sessions, auth, files, health), middleware, WAF |
| `src/services/` (18 files) | Session, event, auth, user, mount, Redis pub/sub |
| `tools/ag3ntum/` (11 tools) | MCP tools: Read/Write/Edit/Bash/Glob/Grep/LS/WebFetch/AskUser/ReadDoc/MultiEdit |
| `src/web_terminal_client/` | React 18.3 + TypeScript 5.6 + Vite 5.4 |

→ See [docs/project-structure.md](docs/project-structure.md) for full directory tree, platform setup, services info.
→ See [docs/source-code-map.md](docs/source-code-map.md) for file-level class/purpose tables.

---

## Security (6-Layer)

| Layer | Component | Scope |
|-------|-----------|-------|
| 0 | WAF (`api/waf_filter.py`) | API requests |
| 1 | Docker (`docker-compose.yml`) | Container |
| 2 | Bubblewrap + UID (`core/sandbox.py`, `core/uid_security.py`) | Bash only |
| 3 | Ag3ntum Tools (`tools/ag3ntum/*`, `core/path_validator.py`) | File/cmd ops |
| 4 | Command Filter (`core/command_security.py`) | Bash cmds |
| 5 | Middleware (`api/security_middleware.py`) | HTTP |
| 6 | Prompts (`prompts/modules/security.md`, `02-security-constraints.md`) | LLM |

- **Fail-closed**: Security load/validate failure → operation denied. Never catch silently.
- **Read-only source**: `src/` mounted `:ro` — agents cannot modify application code.
- **Native tools BLOCKED**: `permissions.yaml` → `tools.disabled`. All ops use `mcp__ag3ntum__*`.

→ See [docs/security-overview.md](docs/security-overview.md) for seccomp, UID isolation, shared GID, WAF, secrets scanning.

---

## Testing

| Suite | Location | Runner | When to run |
|-------|----------|--------|-------------|
| Backend | `tests/backend/` | pytest | API, service, route changes |
| Core | `tests/core-tests/` | pytest | Agent core, tracer, sandbox changes |
| Security | `tests/security/` | pytest | Security, permission, filter changes |
| Sandbox | `tests/sandbox/` | pytest | Bubblewrap, UID, path changes |
| Structural | `tests/structural/` | pytest | Architecture, naming, doc quality (no Docker) |
| E2E | `tests/backend/test_zzz_e2e_server.py` | pytest | Cross-cutting integration changes |
| Frontend | `tests/web_terminal_console/` | vitest | React component, hook changes |

**Markers**: `unit`, `integration`, `slow`, `e2e`. `asyncio_mode = auto`. `--quick` skips E2E + slow. Only `--all` includes E2E.

**CRITICAL**: Before any full test run, rebuild containers from scratch: `./run.sh rebuild --no-cache` then `./run.sh test --all`.

**Full suite** (~10 min, 300K+ lines): `nohup ./run.sh test --all > /tmp/test-all-output.log 2>&1 &`

→ See [docs/testing-guide.md](docs/testing-guide.md) for writing tests, pre-built users, fixtures.
→ See [docs/sse-schema-validation.md](docs/sse-schema-validation.md) for SSE schema tests.

---

## Key Patterns

**Unified execution**: CLI + API → `execute_agent_task(TaskExecutionParams(...))`

**Tracers** (`src/core/tracers/`): `ExecutionTracer` (CLI) | `BackendConsoleTracer` (log) | `EventingTracer` (SSE) | `NullTracer` (test) | `QuietTracer` (minimal)

**Task queue**: Redis-backed, priority scoring. Quotas: 4 global, 2/user, 50/day. Auto-resumes on restart.

**Session storage**: Files (`users/{user}/sessions/{id}/agent.jsonl` + `workspace/`) + SQLite (`sessions` table). `SessionService` syncs.

**Events**: Agent → Redis (real-time, ephemeral) → SSE | Agent → SQLite (persistent) → polling fallback

**Prompts**: `PromptTemplateEngine` — `${VAR}` + Jinja2 (`{% include %}`, `{% if %}`). Auto-loads `prompts/system-prompts/` alphabetically. `PromptManager` handles loading, caching, overrides. Core principles injected into main agent + all subagents.

**MCP server**: Single `ag3ntum` server → `mcp__ag3ntum__ToolName`. Registered in `tools/ag3ntum/__init__.py`.

**Circuit breaker**: 5 consecutive identical failures → trips → FAILED status. `PatternDetector` catches unproductive loops. Both in `TraceProcessor`.

---

## Reselling (Phase 1)

Three-tier hierarchy: Admin (`ag3_adm_`) → Reseller (`ag3_res_`) → End-User. Routes: `/api/v1/reseller/*`, `/api/v1/admin/*`. Services: `APIKeyService`, `ResellerService`, `ResellerQuotaService`, `FeatureFlagService`, `SpendingGuard`, `UsageService`. Spending caps: Platform → Reseller → User (daily/monthly/per-session). Feature flags: Platform → Reseller → User (null = inherit). Settings mode: "readonly" | "configurable". IDOR prevention: `_get_owned_user()` on all reseller endpoints.

→ See `docs/plans/enable-reselling/` for detailed design.

---

## Configuration

**CRITICAL**: Never delete, move, or overwrite `config/*.yaml`. Gitignored, instance-specific, may have credentials. Use temp dirs or mocks.

→ See [docs/configuration.md](docs/configuration.md) for validation tiers, auto-provisioning, user config, external mounts.

---

## Diagnostics & Versioning

Logs: `logs/backend.log` (API, 10MB rotation) | `logs/agent_cli.log` (CLI) | `logs/latest-test-results.log` (last test run). → See [docs/troubleshooting.md](docs/troubleshooting.md)

Version: `VERSION` file (semver). Branch: `main` (dev) | `release` (stable). → See [docs/plans/release-workflow.md](docs/plans/release-workflow.md)

---

## Gotchas

1. **Native tools BLOCKED** — `mcp__ag3ntum__*` only. `permissions.yaml` → `tools.disabled` blocks native tools.
2. **Bubblewrap = Bash only** — File tools use PathValidator in-process; only Bash runs in bwrap with UID drop.
3. **Two event systems** — Redis (real-time, ephemeral) → SSE. SQLite (persistent) → polling fallback + history.
4. **Session dual storage** — Files (SDK jsonl + workspace) + SQLite (queries). `SessionService` syncs both.
5. **Skills symlinked** — `workspace/.claude/skills/` → `/skills/` + `/users/{user}/`
6. **Config → restart** — YAML changes: `./run.sh restart`. Dockerfile/deps/mounts: `./run.sh build`.
7. **Mounts need build** — Both global AND per-user mounts need `./run.sh build`. Only user auth list is dynamic.
8. **Frontend SSE fallback** — SSE → backoff → polling (3+ fails) → SSE retry (60s).
9. **Event dedup** — `Set<number>` on sequence. Duplicates = check backend sequence assignment.
10. **Shared GID** — `ag3ntum_api` in each sandbox user's group. Files 660/770. Adding users requires `./run.sh build`.
11. **Entrypoints sync users** — `/etc/passwd` is ephemeral; entrypoints run `sync_linux_users.py` on start. Test entrypoint creates tester_a (59990) / tester_b (59991).
12. **Supplementary groups set once** — `setpriv --init-groups` at launch. New users need API container restart (`run.sh create-user` handles this). Tests must use pre-built users.
13. **Use `./run.sh test` only** — Never raw `docker exec` (runs as root → false results). run.sh handles test overlay + correct UID + mode restore.
14. **Entrypoint changes need rebuild** — Entrypoints are COPY'd not mounted. `docker compose up -d` reuses containers. Use `./run.sh rebuild`.
15. **Test user UIDs** — Pre-built: 59990/59991 (high end). Dynamic: from 50000. Check `getent passwd` before assigning.
16. **LLM proxy auto-routing** — Models in `llm-api-proxy.yaml` route via internal proxy. Keys in env vars OR `secrets.yaml` → `sandboxed_envs`.
17. **Token revocation is server-side** — Logout increments `token_version`, invalidating all JWTs.
18. **Auth rate limiting** — Redis-based: 5 failed/account/min, 20/IP/min. Fails open if Redis unavailable.
19. **Tool `_*_impl()` functions** — MCP tools have extracted impl functions for testing. Test directly, skip MCP wrapper.
20. **`src/` is read-only** — Mounted `:ro`. Agents cannot modify application source at runtime.
21. **Prompt template engine** — `PromptTemplateEngine`: `${VAR}` + Jinja2. Recursive includes, max depth 5. All prompts are `.md`.
22. **Prompt overrides allowlisted** — Only files in `config/prompt-overrides.yaml`. Security prompts NOT overridable. User overrides in `users/{user}/.prompts/`.
23. **TodoWrite/TodoRead not counted as turns** — Tracked separately via `todo_tool_count` in `TraceProcessor`.
24. **Agent self-assessment** — `determine_session_status()` reads `request_status` headers. Defaults to COMPLETE if none found.
25. **Security refusals = "failed"** — Expected: agent can't complete disallowed requests. Check response for refusal language.
26. **Frontend builder stage** — Multi-stage Dockerfile: `node:20-slim` → bundle → `/web_dist`. Prod serves static; dev uses Vite HMR.
27. **Shared Vite config** — `vite.shared.mjs` shared by `vite.config.mjs` + `vitest.config.mjs`. Entrypoint copies to `/tmp/vite-*/`.
28. **`docker compose exec` = root** — Use `-u 45045:45045` for npm/vite/node to avoid root-owned files breaking entrypoint.
29. **External mount dirs may be empty** — A configured mount pointing to an empty host dir is valid. Report "no files found", do not error or retry.
30. **Rebase before commit** — Before committing, run `git pull --rebase origin main` to pick up changes that landed while working. Long sessions (testing cycles, multi-task batches) are especially prone to main diverging.
31. **Verify commit completeness** — After committing, run `git status` + `git diff`. If unstaged changes remain that belong in the commit, amend or follow up. Then run tests again — pre-commit tests run against the working tree (including unstaged files), so they can pass even when the commit is incomplete.
32. **Reseller user creation = role=user only** — Reseller API hardcodes role='user'. Reseller cannot create admin or reseller accounts.
