# Configuration Quick Reference

All configuration files live in `config/`. Files ending in `.example` are templates; copy them without the `.example` suffix and customize.

## Core Configuration

| File | Purpose | Loaded by | Restart needed |
|------|---------|-----------|----------------|
| `agent.yaml` | Agent behavior: model, max turns, timeout, skills, roles | `AgentConfigLoader` at startup and per-task | `./run.sh restart` |
| `api.yaml` | Server settings: hostname, ports, CORS, Redis URL, rate limits | `load_api_config()` at API startup | `./run.sh restart` |
| `secrets.yaml` | API keys (Anthropic), sandboxed env vars, Fernet key | `AgentConfigLoader` at startup | `./run.sh restart` |

## Optional Configuration

| File | Purpose | Loaded by | Restart needed |
|------|---------|-----------|----------------|
| `subagents.yaml` | Subagent model, tool, and prompt overrides | `SubagentManager` singleton at API startup | `./run.sh restart` |
| `llm-api-proxy.yaml` | Custom LLM routing (OpenAI, local models) | LLM proxy at API startup; models auto-routed | `./run.sh restart` |
| `external-mounts.yaml` | Host folder mounts for agent sessions | `MountService` + Docker volumes at build | `./run.sh build` |
| `user_requirements.txt` | pip packages for user sandbox venvs | Copied to user dir at user creation | New user only |
| `user_secrets.yaml.example` | Template for per-user API keys (`/users/{user}/ag3ntum/secrets.yaml`) | `load_sandboxed_envs()` at task start | New session only |
| `redis.conf` | Redis server settings | Redis container at startup | `./run.sh restart` |

## Security Configuration (`security/`)

| File | Purpose | Loaded by |
|------|---------|-----------|
| `permissions.yaml` | Tool enablement, permission mode, sandbox settings | `PermissionManager` at startup |
| `tools-security.yaml` | PathValidator rules, secrets scanning config | `Ag3ntumPathValidator` per operation |
| `command-filtering.yaml` | 140+ regex rules blocking dangerous commands (16 categories) | `CommandSecurityFilter` per Bash call |
| `upload-filtering.yaml` | MIME type and extension filters for file uploads | Upload route per request |
| `sensitive-data-scanner.yaml` | Patterns for auto-redacting secrets in File Explorer | `SensitiveDataScanner` per file view |
| `seccomp-isolated.json` | Seccomp profile for isolated-mode sandbox (UID 50000-60000) | Docker/bwrap at container start |
| `seccomp-direct.json` | Seccomp profile for direct-mode sandbox | Docker/bwrap at container start |
| `seccomp-container.json` | Container-level seccomp profile | Docker at container start |

## Setup

```bash
# Copy all examples
cp agent.yaml.example agent.yaml
cp api.yaml.example api.yaml
cp secrets.yaml.example secrets.yaml

# Required: Add your Anthropic API key
# Edit secrets.yaml and set anthropic_api_key

# Optional: Configure LLM proxy, external mounts, subagents
cp llm-api-proxy.yaml.example llm-api-proxy.yaml
cp external-mounts.yaml.example external-mounts.yaml
```

## When to Restart vs Rebuild

| Change | Command |
|--------|---------|
| Any YAML config file | `./run.sh restart` |
| `external-mounts.yaml` (add/remove/change paths) | `./run.sh build` |
| Dockerfile, requirements.txt | `./run.sh build --no-cache` |
| Full reset | `./run.sh rebuild` |
