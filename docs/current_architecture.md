# Ag3ntum Current Architecture

## Executive Summary

Ag3ntum is a Claude Code SDK-based AI agent platform that provides two execution modes:
1. **CLI Mode** (`agent_cli.py`) - Direct execution with console tracing
2. **Web UI Mode** (`src/web_terminal_client/`) - Browser-based React terminal interface

Both modes share a common core that handles agent execution, permissions, sessions, and skills. This document describes the current implementation, component interfaces, control flow, and implementation status.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Entry Points: CLI vs Web UI](#2-entry-points-cli-vs-web-ui)
3. [Core Components](#3-core-components)
4. [Control Flow Diagrams](#4-control-flow-diagrams)
5. [Component Interfaces](#5-component-interfaces)
6. [Implementation Status](#6-implementation-status)
7. [File Structure Reference](#7-file-structure-reference)
8. [Web Terminal UI Architecture](#8-web-terminal-ui-architecture)

---

## 1. Architecture Overview

### 1.1 High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            ENTRY POINTS                                      │
│                                                                              │
│   ┌──────────────────┐                         ┌──────────────────────┐      │
│   │scripts/agent_cli │                         │   Web Terminal UI    │      │
│   │  (Direct CLI)    │                         │   (React/Vite)       │      │
│   └────────┬─────────┘                         └──────────┬───────────┘      │
│            │                                              │                  │
│            │ Imports                                      │ HTTP + SSE       │
│            │                                              │                  │
│            ▼                                              ▼                  │
│   ┌──────────────────┐                  ┌────────────────────────────────┐   │
│   │ src/core/agent.py│                  │         src/api/main.py        │   │
│   │ (CLI Entry)      │                  │         (FastAPI App)          │   │
│   └────────┬─────────┘                  └────────────────┬───────────────┘   │
│            │                                             │                   │
└────────────┼─────────────────────────────────────────────┼───────────────────┘
             │                                             │
             │ TaskExecutionParams                         │ TaskExecutionParams
             │ + ExecutionTracer                           │ + EventingTracer
             ▼                                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      UNIFIED TASK RUNNER LAYER                               │
│                                                                              │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │                     execute_agent_task()                           │     │
│   │                    (src/core/task_runner.py)                       │     │
│   │                                                                    │     │
│   │  • Loads AgentConfigLoader                                         │     │
│   │  • Loads PermissionManager from profile                            │     │
│   │  • Builds AgentConfig with merged overrides                        │     │
│   │  • Creates and runs ClaudeAgent                                    │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│                                   │                                          │
└───────────────────────────────────┼──────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          SHARED CORE LAYER                                   │
│                                                                              │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │                        ClaudeAgent                                 │     │
│   │                    (src/core/agent_core.py)                        │     │
│   │                                                                    │     │
│   │  • Builds ClaudeAgentOptions for SDK                              │     │
│   │  • Manages session lifecycle                                       │     │
│   │  • Renders Jinja2 prompts                                          │     │
│   │  • Processes SDK messages via TraceProcessor                       │     │
│   │  • Handles permission callbacks                                    │     │
│   │  • Manages checkpoints                                             │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│             │         │           │            │            │                │
│             ▼         ▼           ▼            ▼            ▼                │
│   ┌─────────┐  ┌───────────┐  ┌─────────┐  ┌───────┐  ┌──────────┐          │
│   │Sessions │  │Permissions│  │ Skills  │  │Schemas│  │  Tracer  │          │
│   │Manager  │  │ Manager   │  │ Manager │  │       │  │          │          │
│   └─────────┘  └───────────┘  └─────────┘  └───────┘  └──────────┘          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL SDK LAYER                                    │
│                                                                              │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │                   Claude Agent SDK (claude_agent_sdk)              │     │
│   │                                                                    │     │
│   │  • ClaudeSDKClient - async context manager                         │     │
│   │  • ClaudeAgentOptions - configuration                              │     │
│   │  • Tool execution (via Ag3ntum MCP tools)                          │     │
│   │  • Native tools blocked, replaced by mcp__ag3ntum__* tools         │     │
│   │  • MCP server integration (Ag3ntum tools with PathValidator)       │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Key Architectural Decisions

| Decision | Description |
|----------|-------------|
| **Dual Entry Points** | CLI for direct execution, Web UI for browser-based execution |
| **Unified Task Runner** | Both entry points use `execute_agent_task()` from `task_runner.py` |
| **Shared Core** | Task runner uses `ClaudeAgent` from `agent_core.py` |
| **File-based Sessions** | Sessions stored in `sessions/` directory with JSON/YAML files |
| **Dual Storage (Web UI)** | SQLite for fast queries + file-based for SDK compatibility |
| **Permission Profiles** | YAML-based permission configuration loaded once at startup |
| **Jinja2 Templates** | System/user prompts rendered from templates in `prompts/` |
| **Tracer Pattern** | Console output via `TracerBase` implementations |
| **SSE Event Streaming** | Real-time execution events via Server-Sent Events |
| **5-Layer Security** | Inbound WAF → Docker → Bubblewrap → Ag3ntumTools → Prompts |
| **Inbound WAF Filter** | Request size limits, text truncation (100K chars), DoS prevention |
| **Command Security Filter** | Regex-based pre-execution filtering of dangerous commands |
| **Ag3ntum MCP Tools** | Native tools blocked; all file/command ops via `mcp__ag3ntum__*` tools |
| **Ag3ntumPathValidator** | Centralized path validation for Python file tools |
| **Bubblewrap Sandbox** | OS-level isolation for subprocess execution (Ag3ntumBash only) |

---

## 2. Entry Points: CLI vs Web UI

### 2.1 Comparison Matrix

| Aspect | CLI (`agent_cli.py`) | Web UI (React) |
|--------|---------------------|----------------|
| **Execution** | Direct, synchronous | Background async task |
| **Task Runner** | `execute_agent_task()` | `execute_agent_task()` (via API) |
| **Console Output** | `ExecutionTracer` (rich) | Custom terminal rendering |
| **Real-time Events** | Direct tracer output | SSE streaming (EventSource) |
| **Session Storage** | File-based only | SQLite + File-based |
| **Authentication** | None | JWT Bearer Token (email/password login) |
| **Configuration** | YAML files + CLI args | YAML + API requests |
| **Result Delivery** | stdout + output file | SSE stream + UI display |
| **Special Commands** | `--show-tools`, etc. | UI buttons & controls |
| **User Interface** | Terminal TUI | Web-based GUI |

### 2.2 Entry Point Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CLI MODE                                          │
│                                                                              │
│   scripts/agent_cli.py  →  src/core/agent.py:main()  →  execute_task()      │
│        │                          │                         │                │
│        │                          ▼                         ▼                │
│        │                 Build TaskExecutionParams    execute_agent_task()   │
│        │                 + ExecutionTracer                  │                │
│        │                                                    ▼                │
│        │                                             ClaudeAgent.run()       │
│        │                                             → Console output        │
│        ▼                                                                     │
│   Returns exit code 0/1                                                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                          WEB UI MODE                                         │
│                                                                              │
│   Browser (React App)                    │   FastAPI Backend (:40080)        │
│   http://localhost:50080                 │                                   │
│        │                                 │                                   │
│        ▼                                 │                                   │
│   [Load config.yaml]                     │                                   │
│   [Check localStorage for token]         │                                   │
│        │                                 │                                   │
│        ▼ (no token)                      │                                   │
│   POST /api/v1/auth/login          ──────▶│   routes/auth.py                 │
│        │  (email + password)             │   └─ Return JWT token             │
│        ▼                                 │                                   │
│   [Store token in localStorage]    ◀─────│                                   │
│        │                                 │                                   │
│        ▼                                 │                                   │
│   [User enters task + clicks Execute]    │                                   │
│        │                                 │                                   │
│        ▼                                 │                                   │
│   POST /api/v1/sessions/run        ──────▶│   routes/sessions.py::run_task() │
│        │                                 │   └─ Start background task        │
│        ▼                                 │                                   │
│   [Receive session_id]             ◀─────│                                   │
│        │                                 │                                   │
│        ▼                                 │                                   │
│   [Connect EventSource to SSE]           │                                   │
│   GET /sessions/{id}/events        ──────▶│   SSE Event Stream               │
│        │                                 │                                   │
│        ▼                                 │   EventingTracer emits:           │
│   [Receive real-time events]       ◀─────│   ├─ agent_start                  │
│   ├─ agent_start → Render box            │   ├─ tool_start / tool_complete   │
│   ├─ tool_start → Show ⚙ + params        │   ├─ thinking / message           │
│   ├─ thinking → Show ❯ + text            │   ├─ output_display               │
│   ├─ agent_complete → Render box         │   └─ agent_complete               │
│        │                                 │                                   │
│        ▼                                 │                                   │
│   [Update UI stats: turns, cost]         │                                   │
│   [Display final result]                 │                                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.3 CLI Argument Definitions

The CLI entry point uses `cli_common.py` for argument parsing:

```python
# Shared argument groups (from cli_common.py):
add_task_arguments()       # --task, --task-file
add_directory_arguments()  # --dir, --add-dir
add_session_arguments()    # --resume, --fork-session, --list-sessions
add_config_override_arguments()  # --model, --max-turns, --timeout, --no-skills
add_permission_arguments() # --profile, --permission-mode
add_role_argument()        # --role
add_output_arguments()     # --output, --json
add_logging_arguments()    # --log-level

# CLI-specific (agent_cli.py only):
add_cli_arguments()        # --config, --secrets, --set, --show-tools, etc.
```

---

## 3. Core Components

### 3.1 Component Dependency Graph

```
┌──────────────────┐          ┌──────────────────────┐
│TaskExecutionParams│         │ execute_agent_task() │
│  (schemas.py)     │────────▶│  (task_runner.py)    │
└──────────────────┘          └──────────┬───────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
                    ▼                    ▼                    ▼
         ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
         │ AgentConfigLoader│ │ PermissionManager│ │   TracerBase     │
         │  (config.py)     │ │(permission_prof.)│ │  (tracer.py)     │
         └────────┬─────────┘ └──────────────────┘ └──────────────────┘
                  │
                  ▼
         ┌──────────────────────┐
         │    AgentConfig       │
         │   (schemas.py)       │
         └──────────┬───────────┘
                    │
                    ▼
         ┌──────────────────────┐
         │     ClaudeAgent      │
         │  (agent_core.py)     │
         └──────────┬───────────┘
                                         │
         ┌───────────────────────────────┼───────────────────────────────┐
         │                               │                               │
         ▼                               ▼                               ▼
┌──────────────────┐          ┌──────────────────────┐       ┌────────────────────┐
│ SessionManager   │          │  PermissionManager   │       │   SkillManager     │
│  (sessions.py)   │          │(permission_profiles) │       │   (skills.py)      │
└────────┬─────────┘          └──────────┬───────────┘       └────────┬───────────┘
         │                               │                            │
         │                               │                            │
         ▼                               ▼                            ▼
┌──────────────────┐          ┌──────────────────────┐       ┌────────────────────┐
│  SessionInfo     │          │  PermissionProfile   │       │      Skill         │
│  (schemas.py)    │          │(permission_profiles) │       │   (skills.py)      │
└──────────────────┘          └──────────────────────┘       └────────────────────┘
                                         │
                                         ▼
                              ┌──────────────────────┐
                              │ PermissionConfig     │
                              │(permission_config.py)│
                              └──────────────────────┘
```

### 3.2 Component Descriptions

| Component | File | Responsibility |
|-----------|------|----------------|
| **execute_agent_task** | `task_runner.py` | Unified entry point for CLI and Web UI execution |
| **TaskExecutionParams** | `schemas.py` | Dataclass for unified execution parameters |
| **ClaudeAgent** | `agent_core.py` | Main agent execution orchestrator + tool registration |
| **AgentConfigLoader** | `src/config.py` | Loads `agent.yaml` + `secrets.yaml` + path constants |
| **PermissionManager** | `permission_profiles.py` | Loads permission profile, manages tool access |
| **PermissionConfig** | `permission_config.py` | Centralized permission schema and tool categories |
| **SessionManager** | `sessions.py` | File-based session CRUD, checkpoints |
| **SkillManager** | `skills.py` | Loads skills from `skills/` directory |
| **TraceProcessor** | `trace_processor.py` | Processes SDK messages for tracing |
| **TracerBase** | `tracer.py` | Abstract interface for execution tracing |
| **ExecutionTracer** | `tracer.py` | Rich interactive CLI output |
| **BackendConsoleTracer** | `tracer.py` | Linear timestamped backend output + logging |
| **EventingTracer** | `tracer.py` | Tracer wrapper that emits events to asyncio queue for SSE |
| **TokenUsage** | `schemas.py` | Token usage statistics model |
| **Checkpoint** | `schemas.py` | Checkpoint data model for conversation rewind |

**Security Components:**

| Component | File | Responsibility |
|-----------|------|----------------|
| **InboundWAFFilter** | `api/waf_filter.py` | Request size limits, text truncation, DoS prevention |
| **CommandSecurityFilter** | `core/command_security.py` | Pre-execution regex filtering of dangerous commands |
| **Ag3ntumPathValidator** | `path_validator.py` | Validates file paths for Ag3ntum file tools |
| **SandboxExecutor** | `sandbox.py` | Builds and executes Bubblewrap commands |
| **SandboxConfig** | `sandbox.py` | Configuration for Bubblewrap sandbox |
| **Ag3ntumBash** | `tools/ag3ntum/ag3ntum_bash/` | Shell command execution with bwrap + command filter |
| **Ag3ntumRead/Write/Edit** | `tools/ag3ntum/ag3ntum_*/` | File operations with PathValidator |

**Database Models (SQLite):**

| Model | Table | Key Fields |
|-------|-------|------------|
| **User** | `users` | id, username, email, password_hash, role, jwt_secret, linux_uid, is_active |
| **Session** | `sessions` | id, user_id, status, task, model, working_dir, num_turns, duration_ms, total_cost_usd |
| **Event** | `events` | id, session_id, sequence, event_type, data, timestamp |
| **Token** | `tokens` | id, user_id, token_type, encrypted_value, description, last_used_at |

**Service Layer (7 services):**

| Service | File | Responsibility |
|---------|------|----------------|
| **AgentRunner** | `agent_runner.py` | Background task execution with EventHub |
| **SessionService** | `session_service.py` | Session CRUD (DB + file sync) |
| **AuthService** | `auth_service.py` | JWT authentication, password hashing |
| **UserService** | `user_service.py` | User CRUD operations |
| **EventService** | `event_service.py` | Event persistence and queries |
| **RedisEventHub** | `redis_event_hub.py` | SSE fanout with Redis pub/sub and backpressure |
| **EncryptionService** | `encryption_service.py` | Token encryption/decryption |

### 3.3 Configuration Files

```
config/
├── agent.yaml                         # Agent configuration (model, max_turns, etc.)
├── secrets.yaml                       # API keys (ANTHROPIC_API_KEY) - from template
├── secrets.yaml.template              # Template for secrets.yaml
├── api.yaml                           # API server configuration (host, port, CORS)
├── llm-api-proxy.yaml                 # LLM API proxy configuration for multi-provider routing
└── security/
    ├── permissions.yaml               # Permission profile (tools.enabled/disabled, sandbox)
    ├── tools-security.yaml            # PathValidator blocklists, readonly paths, network
    ├── command-filtering.yaml         # Command security rules (100+ patterns, 16 categories)
    └── upload-filtering.yaml          # File upload extension/MIME type whitelist/blacklist
```

---

## 4. Control Flow Diagrams

### 4.1 CLI Execution Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CLI EXECUTION FLOW                                    │
└─────────────────────────────────────────────────────────────────────────────┘

scripts/agent_cli.py::main_wrapper()
         │
         ▼
src/core/agent.py::main()
         │
         ├──────────────────────────────────────────────────────────────┐
         │                                                              │
         ▼                                                              │
[Parse Arguments]                                                       │
         │                                                              │
         ▼                                                              │
[Handle Special Commands?]──YES──▶ show_tools() / list_sessions() / ...│
         │                                   │                          │
         NO                                  │                          │
         │                                   ▼                          │
         ▼                              return 0                        │
[Load Configuration]                                                    │
    ├─ AgentConfigLoader.load()                                         │
    ├─ Set ANTHROPIC_API_KEY env var                                   │
    └─ Validate required fields                                         │
         │                                                              │
         ▼                                                              │
[Load Permission Profile]                                               │
    └─ PermissionManager(profile_path)                                  │
         │                                                              │
         ▼                                                              │
[Build AgentConfig]                                                     │
    ├─ Merge YAML config + CLI overrides                               │
    ├─ Get allowed_tools from profile                                   │
    └─ Get auto_checkpoint_tools from profile                           │
         │                                                              │
         ▼                                                              │
execute_task(args, config_loader)                                       │
         │                                                              │
         ▼                                                              │
[Build TaskExecutionParams]                                             │
    ├─ task, working_dir, model, max_turns, ...                         │
    ├─ tracer=ExecutionTracer (rich interactive)                        │
    └─ enable_skills=False if --no-skills                               │
         │                                                              │
         ▼                                                              │
execute_agent_task(params, config_loader)                               │
         │  [task_runner.py - unified entry point]                      │
         ▼                                                              │
[Task Runner Logic]                                                     │
    ├─ Load/Apply config + params overrides                             │
    ├─ Create PermissionManager                                         │
    ├─ Build AgentConfig                                                │
    └─ Create ClaudeAgent with tracer                                   │
         │                                                              │
         ▼                                                              │
ClaudeAgent.run()                                          │
         │                                                              │
         ├────────────────────────────────────────────────────────────────
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ClaudeAgent._execute()                                │
│                                                                              │
│  1. Create/Resume Session (SessionManager)                                   │
│  2. Set session context (PermissionManager.set_session_context)              │
│  3. Copy skills to workspace (if enabled)                                    │
│  4. Load system prompt template (prompts/system.j2)                          │
│  5. Build user prompt template (prompts/user.j2)                             │
│  6. Build ClaudeAgentOptions:                                                │
│     ├─ system_prompt, model, max_turns                                       │
│     ├─ tools, allowed_tools, disallowed_tools                                │
│     ├─ can_use_tool callback (permission checking)                           │
│     ├─ mcp_servers (ag3ntum system tools)                                    │
│     └─ cwd, add_dirs, env                                                    │
│  7. Execute SDK:                                                             │
│     ├─ async with ClaudeSDKClient(options) as client:                        │
│     │     await client.query(user_prompt)                                    │
│     │     async for message in client.receive_response():                    │
│     │         trace_processor.process_message(message)                       │
│     │         checkpoint_tracker.process_message(message)                    │
│  8. Parse output.yaml from workspace                                         │
│  9. Cleanup session (remove skills folder)                                   │
│ 10. Return AgentResult                                                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
[Format Result]
    ├─ JSON output (--json flag)
    └─ Formatted text (default)
         │
         ▼
[Return Exit Code]
    ├─ 0 if status == COMPLETE
    └─ 1 otherwise
```

### 4.2 Security Architecture (5-Layer Defense-in-Depth Model)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   SECURITY ARCHITECTURE (2026-01-11)                         │
└─────────────────────────────────────────────────────────────────────────────┘

User sends HTTP request (API/Web UI only)
         │
         ▼
┌────────────────────────────────────────────┐
│ LAYER 0: Inbound WAF Filter                │
│   → Request body size check (20MB limit)   │
│   → Text content truncation (100K chars)   │
│   → File upload size limit (10MB)          │
│   → Prevents: DoS, memory exhaustion       │
│   → HTTP 413 if limits exceeded            │
└────────────────────────────────────────────┘
         │
         ▼
Task reaches agent execution
         │
         ▼
┌────────────────────────────────────────────┐
│ Tool Availability Check (SDK level)        │
│ Is tool in tools.disabled?                 │
│   YES (Bash, Read, Write, etc.)            │
│       → Tool not available to agent        │
│   NO (mcp__ag3ntum__*, Task, Skill, etc.)  │
│       → Continue                           │
└────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────┐
│ LAYER 4: Command Security Filter           │
│   → Pre-execution regex filtering          │
│   → Blocks: kill, ps, /proc, sudo, etc.    │
│   → 100+ rules across 16 categories        │
│   → config/security/command-filtering.yaml │
└────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────┐
│ LAYER 3: Ag3ntum MCP Tool Execution        │
│                                            │
│ mcp__ag3ntum__Read/Write/Edit/etc.?        │
│   → Ag3ntumPathValidator validates path    │
│   → Blocks: outside workspace, blocklist,  │
│             write to read-only areas       │
│                                            │
│ mcp__ag3ntum__Bash?                        │
│   → LAYER 2: Bubblewrap sandbox wraps cmd  │
│   → Mount namespace isolation              │
│   → PID/IPC/UTS namespace isolation        │
└────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────┐
│ LAYER 2: Bubblewrap Sandbox                │
│   → OS-level process isolation             │
│   → Filesystem namespace (only /workspace) │
│   → Network control (optional)             │
└────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────┐
│ LAYER 1: Docker Container                  │
│   → Host filesystem isolated               │
│   → Only mounted volumes accessible        │
└────────────────────────────────────────────┘
         │
         ▼
    Command/Tool executes safely

┌─────────────────────────────────────────────────────────────────────────────┐
│                           SECURITY LAYERS SUMMARY                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ Layer 5: Prompts           │ Guides agent behavior (soft enforcement)       │
│ Layer 4: Command Filter    │ Regex-based dangerous command blocking         │
│ Layer 3: Ag3ntum Tools     │ PathValidator (file tools), Sandbox (Bash)     │
│ Layer 2: Bubblewrap        │ OS-level subprocess isolation (Ag3ntumBash)    │
│ Layer 1: Docker            │ Container boundary (host protection)           │
│ Layer 0: Inbound WAF       │ Request size limits, DoS prevention (API only) │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.3 Inbound WAF Filter (Layer 0 - API/Web UI Only)

The **Inbound WAF (Web Application Firewall) Filter** protects the backend API from resource exhaustion and DoS attacks by enforcing size limits on incoming requests.

**Implementation:**
- **Middleware**: `waf_middleware` function in `src/api/main.py` (HTTP middleware)
- **Validation Logic**: `validate_request_size` and filter functions in `src/api/waf_filter.py`
- **Validators**: Pydantic field validators in `src/api/models.py`
- **Integration**: Registered as HTTP middleware in `src/api/main.py`

**Protection Limits:**

| Resource | Limit | Action | Status Code |
|----------|-------|--------|-------------|
| Request Body Size | 20 MB | Reject | HTTP 413 |
| Text Content (task field) | 100,000 chars | Truncate | Accept (truncated) |
| File Upload Size | 10 MB | Reject | HTTP 413 |

**How It Works:**

1. **Request Body Middleware** (`waf_middleware` function):
   ```python
   # Checks Content-Length header before reading body
   @app.middleware("http")
   async def waf_middleware(request: Request, call_next):
       await validate_request_size(request)  # Raises HTTPException(413) if > 20MB
       return await call_next(request)
   ```

2. **Text Field Validation** (via `filter_request_data` function):
   ```python
   # Applied to 'task', 'content', 'text' fields in request models
   def truncate_text_content(text: str, field_name: str) -> str:
       if len(text) > MAX_TEXT_CONTENT_CHARS (100K):
           logger.warning(f"Truncating {field_name} from {len} to 100K chars")
           return text[:100_000]  # Truncate and continue
   ```

3. **File Upload Validation** (`validate_file_size` function):
   ```python
   # Called before processing file uploads
   def validate_file_size(content_length: int) -> None:
       if content_length > MAX_FILE_UPLOAD_SIZE (10MB):
           raise HTTPException(413, "File too large")
   ```

**Protected Endpoints:**
- `POST /api/sessions/` (CreateSessionRequest)
- `POST /api/sessions/{session_id}/tasks/run` (RunTaskRequest)
- `POST /api/tasks/run` (StartTaskRequest)

**Benefits:**
- **DoS Prevention**: Prevents attackers from sending massive payloads
- **Memory Protection**: Limits memory consumption per request
- **Graceful Degradation**: Text truncation allows large tasks to proceed (truncated)
- **Early Rejection**: Checks headers before reading full body (efficient)

**Related Documentation:** See `docs/inbound_waf_filter.md` for detailed implementation guide.

---

### 4.4 Ag3ntum MCP Tools

All file and command operations use Ag3ntum MCP tools with built-in security:

| Tool | Security Mechanism | Purpose |
|------|-------------------|---------|
| `mcp__ag3ntum__Read` | PathValidator | Read file contents |
| `mcp__ag3ntum__Write` | PathValidator | Write/create files |
| `mcp__ag3ntum__Edit` | PathValidator | Edit files (search/replace) |
| `mcp__ag3ntum__MultiEdit` | PathValidator | Multiple edits in one call |
| `mcp__ag3ntum__Glob` | PathValidator | Find files by pattern |
| `mcp__ag3ntum__Grep` | PathValidator | Search file contents |
| `mcp__ag3ntum__LS` | PathValidator | List directory contents |
| `mcp__ag3ntum__Bash` | Bubblewrap Sandbox | Execute shell commands |
| `mcp__ag3ntum__WebFetch` | Domain Blocklist | Fetch web content |
| `mcp__ag3ntum__AskUserQuestion` | Event-based HITL | Human-in-the-loop questions |

**Native tools blocked:** `Bash`, `Read`, `Write`, `Edit`, `MultiEdit`, `Glob`, `Grep`, `LS`, `WebFetch`

### 4.5 Human-in-the-Loop (AskUserQuestion)

The `mcp__ag3ntum__AskUserQuestion` tool enables true human-in-the-loop interactions where:

1. **Agent STOPS execution** (not pause/poll) when calling AskUserQuestion
2. **Question stored as event** in the session event stream
3. **User can answer hours/days later** via the frontend
4. **Session can be RESUMED** with the answer using Claude Code's resume capability

**Flow:**
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     HUMAN-IN-THE-LOOP FLOW                                   │
│                                                                              │
│  1. Agent calls mcp__ag3ntum__AskUserQuestion(questions=[...])              │
│     │                                                                        │
│     ▼                                                                        │
│  2. Tool emits "question_pending" event to SSE stream                       │
│     │                                                                        │
│     ▼                                                                        │
│  3. Tool returns STOP signal → Agent execution ends gracefully              │
│     │                                                                        │
│     ▼                                                                        │
│  4. Session status → "waiting_for_input"                                    │
│     │                                                                        │
│     ▼                                                                        │
│  5. Frontend displays question UI (user can take hours/days)                │
│     │                                                                        │
│     ▼ (user answers)                                                         │
│  6. Frontend POSTs to /api/v1/sessions/{id}/answer                          │
│     │                                                                        │
│     ▼                                                                        │
│  7. API emits "question_answered" event                                     │
│     │                                                                        │
│     ▼                                                                        │
│  8. User resumes session → Agent continues with answer in context           │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Event Types:**
| Event Type | When Emitted | Key Data Fields |
|------------|--------------|-----------------|
| `question_pending` | Agent asks a question | `question_id`, `questions` array, `session_id` |
| `question_answered` | User submits answer | `question_id`, `answer`, `session_id` |

**Session Status Addition:**
- `waiting_for_input` - Session stopped, waiting for user answer, resumable

**Frontend Implementation Details:**

The frontend handles AskUserQuestion with special buffering logic to prevent UI flickering:

1. **Buffering Algorithm**: AskUserQuestion tools are buffered during streaming and only attached to messages when `agent_complete` event is received. This prevents the form from "jumping" between messages during live streaming.

2. **Resume Context Hiding**: When a session is resumed with answered questions, the context is wrapped in `<resume-context>...</resume-context>` tags. The frontend's `stripResumeContext()` function removes these tags from display since they are LLM-only content.

3. **Form Interactivity**: The AskUserQuestionBlock form is interactive when:
   - `tool.status === 'running'` (tool still executing), OR
   - `sessionStatus === 'waiting_for_input'` (session waiting for user response)

4. **Answer Submission**: User answers are POSTed to `/api/v1/sessions/{id}/answer`, which emits a `question_answered` event and allows the session to be resumed.

**Related Documentation:** See [docs/ask-user-question-logic.md](ask-user-question-logic.md) for detailed control flow including buffering algorithm, component hierarchy, and API endpoints.

---

## 5. Component Interfaces

### 5.1 Unified Task Runner Interface

```python
# TaskExecutionParams (schemas.py)
@dataclass
class TaskExecutionParams:
    """Unified parameters for agent task execution."""
    task: str
    working_dir: Optional[Path] = None
    session_id: Optional[str] = None           # Pre-generated (for API/Web UI)
    resume_session_id: Optional[str] = None
    fork_session: bool = False
    
    # Config overrides (from agent.yaml if not specified)
    model: Optional[str] = None
    max_turns: Optional[int] = None
    timeout_seconds: Optional[int] = None
    permission_mode: Optional[str] = None
    profile_path: Optional[Path] = None
    role: Optional[str] = None
    additional_dirs: list[str] = field(default_factory=list)
    enable_skills: Optional[bool] = None
    enable_file_checkpointing: Optional[bool] = None
    
    # Tracer (CLI: ExecutionTracer, API/Web UI: BackendConsoleTracer)
    tracer: Optional[TracerBase] = None

# execute_agent_task (task_runner.py)
async def execute_agent_task(
    params: TaskExecutionParams,
    config_loader: Optional[AgentConfigLoader] = None,
) -> AgentResult:
    """
    Single entry point for agent task execution.
    
    Used by both CLI and Web UI entry points for consistent behavior.
    Handles config loading, permission profiles, and ClaudeAgent creation.
    """
```

### 5.2 ClaudeAgent Interface

```python
class ClaudeAgent:
    def __init__(
        self,
        config: AgentConfig,                    # Required configuration
        sessions_dir: Path = SESSIONS_DIR,      # Session storage
        logs_dir: Path = LOGS_DIR,              # Log files
        skills_dir: Path = None,                # Custom skills directory
        tracer: Union[TracerBase, bool] = True, # Console output tracer
        permission_manager: PermissionManager,  # Required permission manager
    ) -> None

    async def run(
        self,
        task: str,                              # Task description
        system_prompt: Optional[str] = None,    # Custom system prompt
        parameters: Optional[dict] = None,      # Template parameters
        resume_session_id: Optional[str] = None,# Resume existing session
        fork_session: bool = False,             # Fork when resuming
        timeout_seconds: Optional[int] = None,  # Override timeout
        session_id: Optional[str] = None,       # Pre-generated session ID (for API)
    ) -> AgentResult

    async def run_with_timeout(...) -> AgentResult  # Alias for run()
    async def compact(session_id: str) -> dict      # Compact conversation history
    
    # Checkpoint management
    def list_checkpoints(session_id: str) -> list[Checkpoint]
    def get_checkpoint(session_id, checkpoint_id, index) -> Optional[Checkpoint]
    def create_checkpoint(session_id, uuid, description) -> Checkpoint
    async def rewind_to_checkpoint(session_id, checkpoint_id, index) -> dict
```

### 5.3 SessionManager Interface

```python
class SessionManager:
    def __init__(self, sessions_dir: Path) -> None
    
    # Session CRUD
    def create_session(working_dir: str, session_id: Optional[str]) -> SessionInfo
    def load_session(session_id: str) -> SessionInfo
    def update_session(session_info, status, resume_id, num_turns, ...) -> SessionInfo
    def list_sessions() -> list[SessionInfo]
    
    # Workspace management
    def get_session_dir(session_id: str) -> Path
    def get_workspace_dir(session_id: str) -> Path
    def get_output_file(session_id: str) -> Path
    def get_log_file(session_id: str) -> Path

    # Skills workspace cleanup
    def cleanup_workspace_skills(session_id) -> None
    
    # Output parsing
    def parse_output(session_id: str) -> dict
    
    # Checkpoint management
    def add_checkpoint(session_info, uuid, type, ...) -> Checkpoint
    def list_checkpoints(session_id) -> list[Checkpoint]
    def get_checkpoint(session_id, checkpoint_id, index) -> Optional[Checkpoint]
    def clear_checkpoints_after(session_info, uuid) -> int
```

### 5.4 PermissionManager Interface (permission_profiles.py)

```python
class PermissionManager:
    def __init__(self, profile_path: Optional[Path] = None) -> None
    
    # Profile management
    def activate() -> PermissionProfile
    def reload_profile() -> None
    
    # Session context (for workspace sandboxing)
    def set_session_context(session_id, workspace_path, workspace_absolute) -> None
    def clear_session_context() -> None
    
    # Permission checking
    def is_allowed(tool_call: str) -> bool
    def needs_confirmation(tool_call: str) -> bool
    
    # Tool access
    def get_enabled_tools() -> list[str]
    def get_permission_checked_tools() -> set[str]
    def get_disabled_tools() -> set[str]
    def get_pre_approved_tools() -> list[str]
    def get_allowed_dirs() -> list[str]
    
    # Tracing
    def set_tracer(tracer: TracerBase) -> None
```

### 5.5 TracerBase Interface

```python
class TracerBase(ABC):
    @abstractmethod
    def on_agent_start(session_id, model, tools, working_dir, skills, task) -> None
    
    @abstractmethod
    def on_tool_start(tool_name, tool_input, tool_id) -> None
    
    @abstractmethod
    def on_tool_complete(tool_name, tool_id, result, duration_ms, is_error) -> None
    
    @abstractmethod
    def on_thinking(thinking_text: str) -> None
    
    @abstractmethod
    def on_message(text: str, is_partial: bool) -> None
    
    @abstractmethod
    def on_error(error_message: str, error_type: str) -> None
    
    @abstractmethod
    def on_agent_complete(status, num_turns, duration_ms, cost, result, ...) -> None
    
    @abstractmethod
    def on_output_display(output, error, comments, result_files, status) -> None
    
    @abstractmethod
    def on_profile_switch(profile_type, profile_name, tools, allow_count, deny_count) -> None
    
    @abstractmethod
    def on_hook_triggered(hook_event, tool_name, decision, message) -> None
    
    @abstractmethod
    def on_conversation_turn(turn, prompt, response, duration, tools) -> None
    
    @abstractmethod
    def on_session_connect(session_id) -> None
    
    @abstractmethod
    def on_session_disconnect(session_id, total_turns, total_duration_ms) -> None
```

**Implementations:**
- `ExecutionTracer` - Full console output with colors, spinners, boxes (CLI)
- `BackendConsoleTracer` - Linear timestamped output + Python logging (HTTP backend)
- `EventingTracer` - Wrapper tracer that emits structured events to EventHub for SSE streaming
- `NullTracer` - No output (for testing)

### 5.6 API Service Interfaces

```python
# AgentRunner (services/agent_runner.py)
class AgentRunner:
    async def start_task(params: TaskParams) -> None
    async def cancel_task(session_id: str) -> bool
    def is_running(session_id: str) -> bool
    def is_cancellation_requested(session_id: str) -> bool
    def get_result(session_id: str) -> Optional[dict]
    async def subscribe(session_id: str) -> asyncio.Queue  # Subscribe to EventHub
    async def unsubscribe(session_id: str, queue: asyncio.Queue) -> None
    async def publish_event(session_id: str, event: dict) -> None
    def cleanup_session(session_id: str) -> None

# SessionService (services/session_service.py)
class SessionService:
    async def create_session(db, user_id, task, sessions_dir, model) -> Session
    async def get_session(db, session_id, user_id) -> Optional[Session]
    async def list_sessions(db, user_id, limit, offset) -> tuple[list[Session], int]
    async def update_session(db, session, status, ...) -> Session
    def get_session_info(session_id: str) -> dict
    def get_session_file(session_id: str, path: str) -> Path

# RedisEventHub (services/redis_event_hub.py)
class RedisEventHub:
    async def subscribe(session_id: str) -> asyncio.Queue
    async def unsubscribe(session_id: str, queue: asyncio.Queue) -> None
    async def publish(session_id: str, event: dict) -> None

# EventService (services/event_service.py)
async def record_event(event: dict) -> None
async def list_events(session_id, after_sequence, limit) -> list[dict]
async def get_last_sequence(session_id: str) -> int
async def get_latest_terminal_status(session_id: str) -> Optional[str]
```

### 5.7 SSE Event Streaming Architecture

**EventingTracer** - Wrapper pattern for real-time event emission:

```python
class EventingTracer(TracerBase):
    """Tracer wrapper that emits structured events via EventHub."""

    def __init__(
        self,
        tracer: TracerBase,                    # Wrapped tracer (BackendConsoleTracer)
        event_queue: EventSinkQueue,           # EventHub/RedisEventHub sink for publishing
        event_sink: Callable[[dict], Awaitable[None]],  # DB persistence callback
        session_id: str,
        initial_sequence: int = 0,
    ) -> None

    def emit_event(event_type: str, data: dict[str, Any]) -> None
    # All TracerBase methods (on_agent_start, on_tool_start, etc.)
    # Call wrapped tracer + publish to Redis/EventHub + persist to DB
```

**EventHub** - In-memory pub/sub fanout for SSE (single-server mode):

```python
class EventHub:
    """In-memory event hub for single-server deployments."""

    async def subscribe(session_id: str) -> asyncio.Queue  # Create subscriber queue
    async def unsubscribe(session_id: str, queue: asyncio.Queue) -> None
    async def publish(session_id: str, event: dict) -> None  # Fanout to all subscribers
```

**RedisEventHub** - Redis-based pub/sub for horizontal scaling:

```python
class RedisEventHub:
    """Redis-based event hub for cross-container SSE streaming."""

    async def subscribe(session_id: str) -> asyncio.Queue
    # Creates local queue + background Redis listener task
    # Channel pattern: session:{session_id}:events

    async def unsubscribe(session_id: str, queue: asyncio.Queue) -> None
    # Cancels background listener task

    async def publish(session_id: str, event: dict) -> None
    # Publishes to Redis Pub/Sub channel
    # All containers receive the event in real-time
```

**Redis Configuration** (see `config/redis.conf`):
- **Port binding**: `127.0.0.1:46379` (external) → `6379` (internal)
- **Security**: Localhost-only access, no external network exposure
- **Memory limit**: 256MB with LRU eviction policy
- **Disabled commands**: `KEYS` (performance), `SHUTDOWN`, `DEBUG`
- **No persistence**: Ephemeral events only (SQLite stores history)

**Feature Flag** (`config/api.yaml`):
```yaml
features:
  redis_sse: false  # Default: in-memory EventHub (single-server)
                    # Set true: RedisEventHub (horizontal scaling)
```

**Event Structure:**
```json
{
  "type": "agent_start | tool_start | tool_complete | thinking | message | error | agent_complete | cancelled | ...",
  "data": {
    // Event-specific data
  },
  "timestamp": "2026-01-04T12:34:56.789Z",
  "sequence": 123,
  "session_id": "20260104_123456_abc123"
}
```

**SSE Endpoint Flow:**
1. Client connects to `GET /sessions/{id}/events?token={jwt}`
2. Server validates token and subscribes to EventHub
3. Server replays missed events from database (after_sequence)
4. Server streams live events as they're published to EventHub
5. Heartbeat sent every 30s if no events (`: heartbeat\n\n`)
6. Stream ends on `agent_complete`, `error`, or `cancelled` events
7. Subscriber automatically unsubscribed when stream ends

**Event Delivery Guarantee:**
- **Publish-then-persist**: Events published to Redis/EventHub first (~1ms), then persisted to SQLite (~50ms)
- **10-event overlap buffer**: SSE replays from `sequence - 10` to catch late-arriving events
- **Deduplication**: Events deduplicated by sequence number to prevent duplicates
- **No race condition**: Overlap buffer + deduplication ensures no events are lost

**Event Flow:**
1. EventingTracer emits event → publishes to Redis (~1ms)
2. Event persisted to SQLite in background (~50ms)
3. SSE subscribers receive event immediately from Redis
4. Late subscribers replay from DB with 10-event overlap
5. Duplicate events filtered by sequence number

**Client Fallback:**
- **Web UI**: SSE streaming for real-time events (Redis required)
- **History endpoint**: `/events/history` available for polling fallback
- **CLI**: Always uses direct `ExecutionTracer` (no SSE needed)

**Horizontal Scaling:**
- **RedisEventHub**: Cross-container event delivery via Redis Pub/Sub
- **Port security**: Redis bound to `127.0.0.1:46379` (localhost only)
- **Redis required**: System fails to start with clear error if Redis unavailable
- **See**: `docs/redis_security.md` for complete security configuration

---

## 6. Implementation Status

### 6.1 Implemented Features

| Feature | CLI | Web UI | Notes |
|---------|-----|--------|-------|
| Task execution | ✅ | ✅ | Both use ClaudeAgent.run() |
| Session creation | ✅ | ✅ | File-based + SQLite (Web UI) |
| Session resumption | ✅ | ✅ | Via --resume / resume_session_id |
| Session forking | ✅ | ✅ | Via --fork-session flag |
| List sessions | ✅ | ✅ | --list-sessions / GET /sessions |
| Permission profiles | ✅ | ✅ | YAML-based configuration |
| Skills system | ✅ | ✅ | Markdown + optional scripts |
| File checkpointing | ✅ | ✅ | Auto-checkpoints on Write/Edit |
| Checkpoint rewind | ✅ | ❌ | API endpoint not exposed |
| Console tracing | ✅ | N/A | ExecutionTracer for CLI |
| SSE event streaming | N/A | ✅ | Real-time events via /sessions/{id}/events |
| JSON output | ✅ | ✅ | --json flag / API response |
| Config overrides | ✅ | ✅ | CLI args / request body |
| Task cancellation | ❌ | ✅ | POST /sessions/{id}/cancel |
| Authentication | N/A | ✅ | JWT Bearer tokens |
| MCP tools | ✅ | ✅ | ag3ntum MCP server (Read, Write, Bash, etc.) |

### 6.2 Feature Status

| Feature | Status | Notes |
|---------|--------|-------|
| SSE streaming | ✅ **Completed (Stage 2)** | Real-time execution events via SSE |
| Web terminal UI | ✅ **Completed (Stage 3)** | React/TypeScript terminal with SSE |
| Multi-agent context sharing | 🔄 Future | Shared session access |
| Session archival to DB | 🔄 Future | PostgreSQL session storage |
| Docker deployment | 🔄 Future | Containerized deployment |
| Version endpoint | ❌ Not implemented | `<yyyymmdd>-<commit>` format |
| PostgreSQL migration | 🔄 Future | Replace SQLite |
| Webhook notifications | ❌ Not implemented | Event callbacks |

### 6.3 Known Gaps

1. **Checkpoint rewind not exposed via API** - Only available through CLI/direct ClaudeAgent use
2. **No session archival** - Sessions remain in file system indefinitely
3. **SQLite limitations** - Single-file database, not suitable for high concurrency
4. **No distributed session sharing** - Sessions are file-local, no cross-instance access

---

## 7. File Structure Reference

```
Project/
├── scripts/
│   ├── agent_cli.py          # CLI entry point (thin wrapper)
│   └── ag3ntum_debug.py      # CLI debugging tool for security testing
├── config/
│   ├── agent.yaml            # Agent configuration
│   ├── api.yaml              # API server configuration
│   ├── secrets.yaml          # API keys (from template)
│   ├── secrets.yaml.template # Template for secrets
│   ├── llm-api-proxy.yaml    # LLM API proxy configuration
│   └── security/
│       ├── permissions.yaml      # Permission profile (tools.enabled/disabled, sandbox)
│       ├── tools-security.yaml   # PathValidator config (blocklists, readonly paths)
│       ├── command-filtering.yaml  # Command security rules (100+ patterns)
│       └── upload-filtering.yaml   # File upload extension/MIME whitelist/blacklist
├── prompts/
│   ├── system.j2             # System prompt template
│   ├── user.j2               # User prompt template
│   ├── roles/
│   │   └── default.md        # Default role definition
│   └── modules/              # Prompt template modules
│       ├── identity.j2       # Agent identity module
│       ├── execution.j2      # Execution guidance module
│       ├── output.j2         # Output formatting module
│       ├── context_management.j2  # Context management module
│       ├── security.j2       # Security guidance module
│       ├── skills.j2         # Skills module
│       └── tools.j2          # Tools module
├── sessions/                 # Session storage (CLI mode)
│   └── {session_id}/
│       ├── session_info.json # Session metadata
│       ├── agent.jsonl       # SDK message log
│       └── workspace/
│           ├── output.yaml   # Agent output
│           └── skills/       # Copied skills (during execution)
├── skills/                   # Available skills
│   └── {skill_name}/
│       ├── {skill_name}.md   # Skill description
│       └── scripts/          # Optional scripts
├── src/
│   ├── __init__.py
│   ├── config.py             # Configuration loading + path constants
│   ├── api/                  # FastAPI application
│   │   ├── __init__.py
│   │   ├── main.py           # App factory + WAF middleware
│   │   ├── deps.py           # Dependency injection (JWT, DB)
│   │   ├── models.py         # Pydantic request/response models (with WAF validators)
│   │   ├── waf_filter.py     # Inbound WAF filter (size limits, truncation)
│   │   ├── llm_proxy/        # LLM API proxy for multi-provider routing
│   │   │   ├── __init__.py
│   │   │   ├── config.py     # Proxy configuration
│   │   │   └── translator.py # Provider-specific translation
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── auth.py       # POST /auth/login, POST /auth/logout, GET /auth/me
│   │       ├── health.py     # GET /health, GET /config
│   │       ├── sessions.py   # /sessions/* endpoints (10 routes)
│   │       └── llm_proxy.py  # LLM proxy endpoints
│   ├── cli/                  # CLI utilities
│   │   └── create_user.py    # User creation CLI tool
│   ├── core/                 # Core agent logic (25 files)
│   │   ├── __init__.py
│   │   ├── __main__.py       # Package entry point
│   │   ├── agent.py          # CLI entry point logic
│   │   ├── agent_core.py     # ClaudeAgent implementation + tool registration
│   │   ├── cli_common.py     # Shared CLI arguments
│   │   ├── command_security.py # CommandSecurityFilter for dangerous commands
│   │   ├── constants.py      # UI constants (colors, symbols, box chars)
│   │   ├── conversation.py   # Conversation utilities
│   │   ├── exceptions.py     # Custom exceptions
│   │   ├── hooks.py          # SDK hooks (simplified)
│   │   ├── logging_config.py # Logging setup
│   │   ├── output.py         # Output formatting
│   │   ├── path_validator.py # Ag3ntumPathValidator for file tool security
│   │   ├── permission_config.py   # Permission configuration
│   │   ├── permission_profiles.py # PermissionManager
│   │   ├── permissions.py    # Permission callback factory
│   │   ├── sandbox.py        # Bubblewrap sandbox executor
│   │   ├── schemas.py        # Pydantic data models + TaskExecutionParams
│   │   ├── sessions.py       # SessionManager
│   │   ├── skills.py         # SkillManager
│   │   ├── skill_tools.py    # Skill execution support
│   │   ├── structured_output.py  # JSON/YAML parsing utilities
│   │   ├── task_runner.py    # Unified execute_agent_task()
│   │   ├── tasks.py          # Task loading from files
│   │   ├── tool_utils.py     # Tool helper functions
│   │   ├── trace_processor.py # SDK message processor
│   │   └── tracer.py         # TracerBase + ExecutionTracer + BackendConsoleTracer + EventingTracer
│   ├── db/                   # Database layer
│   │   ├── __init__.py
│   │   ├── database.py       # SQLAlchemy AsyncSession setup
│   │   └── models.py         # User, Session, Event, Token models (4 tables)
│   ├── services/             # Business logic (7 services)
│   │   ├── __init__.py
│   │   ├── agent_runner.py   # Background task runner with RedisEventHub
│   │   ├── auth_service.py   # JWT authentication
│   │   ├── user_service.py   # User CRUD operations
│   │   ├── session_service.py # Session CRUD service
│   │   ├── event_service.py  # Event persistence
│   │   ├── redis_event_hub.py # Redis-based SSE fanout (required)
│   │   └── encryption_service.py  # Token encryption
│   └── web_terminal_client/  # React Web UI (Stage 3)
│       ├── index.html        # HTML entry point
│       ├── package.json      # npm dependencies
│       ├── vite.config.ts    # Vite dev server (port 50080)
│       ├── tsconfig.json     # TypeScript configuration
│       ├── public/
│       │   └── config.yaml   # Frontend config (API URL, UI settings)
│       └── src/
│           ├── main.tsx      # React entry point
│           ├── App.tsx       # Main component with routing
│           ├── LoginPage.tsx # JWT authentication page
│           ├── AuthContext.tsx  # Auth state management
│           ├── ProtectedRoute.tsx  # Route protection
│           ├── api.ts        # REST API client
│           ├── sse.ts        # SSE event streaming
│           ├── config.ts     # Config loader
│           ├── types.ts      # TypeScript types
│           └── styles.css    # Dark terminal theme
├── tools/                    # MCP tools
│   ├── __init__.py           # Package init
│   └── ag3ntum/
│       ├── __init__.py       # Tool exports + MCP server factory
│       ├── ag3ntum_file_tools.py  # Shared file tool utilities
│       ├── ag3ntum_bash/     # mcp__ag3ntum__Bash (bwrap sandbox)
│       │   ├── __init__.py
│       │   └── tool.py
│       ├── ag3ntum_read/     # mcp__ag3ntum__Read (PathValidator)
│       │   ├── __init__.py
│       │   └── tool.py
│       ├── ag3ntum_write/    # mcp__ag3ntum__Write (PathValidator)
│       │   ├── __init__.py
│       │   └── tool.py
│       ├── ag3ntum_edit/     # mcp__ag3ntum__Edit (PathValidator)
│       │   ├── __init__.py
│       │   └── tool.py
│       ├── ag3ntum_multiedit/# mcp__ag3ntum__MultiEdit (PathValidator)
│       │   ├── __init__.py
│       │   └── tool.py
│       ├── ag3ntum_glob/     # mcp__ag3ntum__Glob (PathValidator)
│       │   ├── __init__.py
│       │   └── tool.py
│       ├── ag3ntum_grep/     # mcp__ag3ntum__Grep (PathValidator)
│       │   ├── __init__.py
│       │   └── tool.py
│       ├── ag3ntum_ls/       # mcp__ag3ntum__LS (PathValidator)
│       │   ├── __init__.py
│       │   └── tool.py
│       ├── ag3ntum_webfetch/ # mcp__ag3ntum__WebFetch (domain blocklist)
│       │   ├── __init__.py
│       │   └── tool.py
│       └── ag3ntum_ask/      # mcp__ag3ntum__AskUserQuestion (HITL)
│           ├── __init__.py
│           └── tool.py       # Event-based question/answer flow
└── tests/                    # Test suites
```

---

## Summary

Ag3ntum provides a well-structured dual-entry architecture where:

1. **Unified Task Runner** (`task_runner.py`) provides a single entry point for CLI and Web UI
2. **CLI Mode** provides direct execution with rich interactive console output (`ExecutionTracer`)
3. **Web UI Mode** provides browser-based terminal with React/TypeScript and SSE streaming

The key components (ClaudeAgent, SessionManager, PermissionManager, SkillManager) are designed with clear interfaces and separation of concerns. The tracer pattern allows different output strategies for CLI vs API modes, while the unified task runner ensures consistent behavior across all entry points.

**Security Architecture (5-Layer Defense-in-Depth Model):**

| Layer | Component | Responsibility | Applies To |
|-------|-----------|----------------|------------|
| 0 | Inbound WAF | Request size limits, text truncation, DoS prevention | API/Web UI only |
| 1 | Docker | Host isolation, container boundary | All modes |
| 2 | Bubblewrap | Subprocess isolation (Ag3ntumBash only) | All modes |
| 3 | Ag3ntum Tools | PathValidator for file tools, command filter for Bash | All modes |
| 4 | Command Filter | Regex-based dangerous command blocking (100+ rules) | All modes |
| 5 | Prompts | Agent guidance (soft enforcement) | All modes |

**Key Security Features:**
- **Inbound WAF** (API/Web UI): Request size limits (20MB), text truncation (100K chars), file upload limits (10MB)
- **Command Security Filter**: Pre-execution regex filtering blocks `kill`, `ps`, `/proc`, `sudo`, and 100+ dangerous patterns
- Native Claude Code tools (`Bash`, `Read`, `Write`, etc.) are **blocked**
- All file/command operations go through `mcp__ag3ntum__*` tools
- `Ag3ntumPathValidator` ensures workspace confinement for Python file tools
- Bubblewrap provides OS-level isolation for subprocess execution
- Skills mounted inside `/workspace/skills` for unified view

**Architecture Benefits:**
- Single source of truth for task execution logic
- Two entry points sharing the same backend
- Consistent behavior guaranteed across CLI and Web UI
- Real-time SSE streaming for Web UI mode with EventHub pub/sub
- Comprehensive 6-layer security architecture (Layers 0-5)

**Implementation Status:**
- ✅ Stage 1: CLI mode (COMPLETED)
- ✅ Stage 2: SSE streaming for real-time execution events (COMPLETED)
- ✅ Stage 3: React web terminal UI (COMPLETED)
- ✅ Stage 4: 5-layer security architecture (COMPLETED)
  - ✅ Inbound WAF filter for request size limiting
  - ✅ Command security filter (100+ regex rules, 16 categories)
  - ✅ Path validator for file tools
  - ✅ Bubblewrap sandbox for command execution
  - ✅ Security-hardened prompts (no implementation disclosure)
- 🔄 Future: Multi-agent context sharing, PostgreSQL migration

---

## 8. Web Terminal UI Architecture

### 8.1 Technology Stack

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Frontend Framework** | React | 18.3.1 | UI component library |
| **Language** | TypeScript | 5.6.3 | Type-safe development |
| **Build Tool** | Vite | 5.4.11 | Fast dev server & bundler |
| **API Client** | Fetch API | Native | REST API communication |
| **SSE Client** | EventSource API | Native | Real-time event streaming |
| **Configuration** | YAML | 2.5.1 | Frontend config loading |

### 8.2 Component Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         WEB TERMINAL UI ARCHITECTURE                         │
└─────────────────────────────────────────────────────────────────────────────┘

                              BROWSER (http://localhost:50080)
                                         │
                    ┌────────────────────┴────────────────────┐
                    │                                         │
                    ▼                                         ▼
         ┌────────────────────┐                  ┌────────────────────┐
         │    App.tsx         │                  │   config.yaml      │
         │  (Main Component   │                  │  (UI Config)       │
         │   with Routing)    │                  └────────────────────┘
         └──────────┬─────────┘
                    │
      ┌─────────────┼─────────────┬─────────────┬──────────────┐
      │             │             │             │              │
      ▼             ▼             ▼             ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│LoginPage │  │AuthContext│ │Protected │  │ api.ts   │  │ sse.ts   │
│.tsx      │  │.tsx      │  │Route.tsx │  │(REST API)│  │(SSE      │
│(Login)   │  │(Auth     │  │(Route    │  │          │  │ Stream)  │
│          │  │ State)   │  │ Guard)   │  │          │  │          │
└──────────┘  └──────────┘  └──────────┘  └─────┬────┘  └─────┬────┘
                                                │             │
      ┌─────────────────────────────────────────┼─────────────┘
      │                                         │
      ▼                                         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    FastAPI Backend (http://localhost:40080)                  │
│                                                                              │
│   REST API Endpoints:                    SSE Endpoint:                       │
│   ├─ POST /api/v1/auth/login            GET /api/v1/sessions/{id}/events    │
│   ├─ POST /api/v1/auth/logout           │                                   │
│   ├─ GET  /api/v1/auth/me               │ Real-time event stream            │
│   ├─ POST /api/v1/sessions/run          │ (EventingTracer → EventHub → SSE) │
│   ├─ POST /api/v1/sessions              │                                   │
│   ├─ GET  /api/v1/sessions              │                                   │
│   ├─ GET  /api/v1/sessions/{id}         │                                   │
│   ├─ POST /api/v1/sessions/{id}/task    │                                   │
│   ├─ POST /api/v1/sessions/{id}/cancel  │                                   │
│   ├─ GET  /api/v1/sessions/{id}/result  │                                   │
│   ├─ GET  /api/v1/sessions/{id}/events/history                              │
│   └─ GET  /api/v1/sessions/{id}/files   │                                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 8.3 Configuration Files

**Backend: `config/api.yaml`**
```yaml
api:
  host: "0.0.0.0"
  port: 40080
  cors_origins:
    - "http://localhost:50080"    # Frontend origin
    - "http://127.0.0.1:50080"
```

**Frontend: `src/web_terminal_client/vite.config.ts`**
```typescript
export default defineConfig({
  plugins: [react()],
  server: {
    port: 50080,
    host: '0.0.0.0',
  },
});
```

**Frontend Config: `src/web_terminal_client/public/config.yaml`**
```yaml
server:
  port: 50080
  host: "0.0.0.0"

api:
  base_url: "http://localhost:40080"

ui:
  max_output_lines: 1000
  auto_scroll: true
```

### 8.4 Component Interfaces

**REST API Client (`api.ts`):**
```typescript
login(baseUrl: string, email: string, password: string): Promise<TokenResponse>
logout(baseUrl: string, token: string): Promise<void>
getCurrentUser(baseUrl: string, token: string): Promise<UserResponse>
listSessions(baseUrl: string, token: string): Promise<SessionListResponse>
getSession(baseUrl: string, token: string, sessionId: string): Promise<SessionResponse>
runTask(baseUrl: string, token: string, task: string): Promise<TaskStartedResponse>
cancelSession(baseUrl: string, token: string, sessionId: string): Promise<void>
getResult(baseUrl: string, token: string, sessionId: string): Promise<ResultResponse>
```

**SSE Client (`sse.ts`):**
```typescript
connectSSE(
  baseUrl: string,
  sessionId: string,
  token: string,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void
): () => void  // Returns cleanup function
```

### 8.5 SSE Event Type Rendering

The Web UI renders all SSE event types from `EventingTracer`:

| SSE Event Type | UI Rendering | Description |
|----------------|-------------|-------------|
| `agent_start` | Box with session ID, model | Agent initialization |
| `tool_start` | ⚙ Tool name + input params | Tool execution begins |
| `tool_complete` | └─ Tool status (OK/FAILED) + duration | Tool execution ends |
| `thinking` | ❯ Thinking text | Claude's reasoning |
| `message` | ✦ Message text | Agent messages |
| `profile_switch` | Profile name + rule counts | Permission profile change |
| `output_display` | Output YAML content box | Final output display |
| `agent_complete` | Box with status, metrics | Task completion |
| `error` | ✖ Error message | Error occurred |
| `cancelled` | ● Cancelled message | Task cancelled |
| `conversation_turn` | (Updates turn counter) | Turn tracking |

### 8.6 UI Features

| Feature | Implementation | Notes |
|---------|---------------|-------|
| **Login** | LoginPage.tsx with email/password | JWT stored in localStorage |
| **Protected Routes** | ProtectedRoute.tsx | Redirects to login if not authenticated |
| **Auth State** | AuthContext.tsx | React context for auth management |
| **Task Submission** | Textarea + Execute button | Ctrl+Enter shortcut |
| **Real-time Execution** | SSE streaming via `EventSource` | Live tool execution, thinking |
| **Session Management** | Dropdown to select/switch sessions | Automatic session list refresh |
| **Task Cancellation** | Cancel button during execution | POST /sessions/{id}/cancel |
| **Status Display** | Footer with turn/token/cost metrics | Real-time updates |
| **Session History** | Dropdown with session list | Click to load past sessions |
| **Terminal Output** | Scrollable event log | Auto-scroll, max 1000 lines |
| **Dark Theme** | Terminal-style CSS | Black background, monospace font |

### 8.7 Development Setup

**VSCode Launch Configurations (`.vscode/launch.json`):**

```json
{
  "configurations": [
    {
      "name": "Backend API Server",
      "type": "debugpy",
      "module": "uvicorn",
      "args": ["src.api.main:app", "--host", "0.0.0.0", "--port", "40080", "--reload"]
    },
    {
      "name": "Web UI (React/Vite)",
      "type": "node-terminal",
      "command": "npm run dev",
      "cwd": "${workspaceFolder}/Project/src/web_terminal_client"
    }
  ],
  "compounds": [
    {
      "name": "Full Stack (Backend + Web UI)",
      "configurations": ["Backend API Server", "Web UI (React/Vite)"]
    }
  ]
}
```

**Setup Process:**

1. Install Frontend Dependencies:
   ```bash
   cd Project/src/web_terminal_client
   npm install
   ```

2. Start Full Stack (VSCode):
   - Press `F5` and select "Full Stack (Backend + Web UI)"
   - Both services start automatically
   - Browser opens to http://localhost:50080

3. Manual Start:
   ```bash
   # Terminal 1 - Backend
   cd Project && source venv/bin/activate
   uvicorn src.api.main:app --host 0.0.0.0 --port 40080 --reload

   # Terminal 2 - Frontend
   cd Project/src/web_terminal_client
   npm run dev
   ```

### 8.8 Access URLs

| Service | URL | Description |
|---------|-----|-------------|
| **Web UI** | http://localhost:50080 | React terminal interface |
| **Backend API** | http://localhost:40080 | FastAPI REST endpoints |
| **API Docs (Swagger)** | http://localhost:40080/api/docs | Interactive API documentation |
| **API Docs (ReDoc)** | http://localhost:40080/api/redoc | Alternative API documentation |

### 8.9 Missing UI Features (Not Yet Implemented)

| Feature | Priority | Notes |
|---------|----------|-------|
| **Resume/Fork Sessions** | Medium | API supports it, UI doesn't expose |
| **Working Directory Selector** | Medium | Currently uses default |
| **Config Overrides** | Low | Model, max_turns, timeout, etc. |
| **Role Selection** | Low | Role picker dropdown |
| **Permission Profile Picker** | Low | Profile dropdown |
| **Checkpoint UI** | Low | View/rewind checkpoints |
| **Session Export** | Low | Download session as JSON |

