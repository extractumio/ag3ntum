# Ag3ntum Project Structure

## Directory Tree

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
│   │   ├── routes/reseller.py     # Reseller API endpoints
│   │   ├── routes/admin.py        # Admin API endpoints
│   │   └── reseller_models.py     # Reseller/admin Pydantic models
│   ├── services/                  # Business logic (25 files, +7 reseller)
│   │   ├── api_key_service.py     # API key CRUD
│   │   ├── reseller_service.py    # Reseller CRUD
│   │   ├── feature_flag_service.py # Flag resolution
│   │   ├── spending_guard.py      # Spending caps
│   │   └── usage_service.py       # Usage recording
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
├── docker-compose.yml             # api + web + redis (prod: static server)
├── docker-compose.dev.yml         # Overrides web for Vite dev server
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

## Source Code

- **Core** (`src/core/`, 40 files) — Agent orchestration, sandbox, security, prompts, tracers
- **API** (`src/api/`) — FastAPI app, routes (sessions, auth, files, health, reseller, admin), middleware, WAF
- **Services** (`src/services/`, 25 files) — Session, event, auth, user, mount, API keys, reseller, spending, usage, Redis pub/sub
- **MCP Tools** (`tools/ag3ntum/`, 11 tools) — Sandboxed replacements for native tools (Read/Write/Edit/Bash/Glob/Grep/LS/WebFetch/AskUserQuestion/ReadDocument/MultiEdit)
- **Web Terminal** (`src/web_terminal_client/`) — React 18.3 + TypeScript 5.6 + Vite 5.4

→ See [source-code-map.md](source-code-map.md) for file-level class/purpose tables.

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

### Services

`ag3ntum-api` (uvicorn) + `ag3ntum-web` (prod: static server, dev: vite) + `redis` (7-alpine)

Capabilities: SYS_ADMIN, SETUID, SETGID, CHOWN. CPU-specific numpy/pandas (SSE4.2 detection). ARM64 supported.

Python 3.13+ | AGPL-3.0 | claude-agent-sdk 0.1.23 | Ubuntu 24.04 container
