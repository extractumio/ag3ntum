# Configuration

---

## Config Validation & Auto-Provisioning

Config files (`config/*.yaml`) are gitignored — they hold instance-specific settings and credentials. They can be lost to `git clean -fdx` or other automated operations.

`run.sh` validates all config files before every `build`/`rebuild` via `validate_and_provision_configs()`, driven by the `CONFIG_REGISTRY` array at the top of the script.

**Two tiers:**

| Tier | Behavior when missing | Files |
|------|----------------------|-------|
| `REQUIRED_SECRET` | Fatal error with `cp` instructions | `secrets.yaml` |
| `REQUIRED_SAFE` | Auto-created from `.example` template + INFO message | `agent.yaml`, `api.yaml`, `external-mounts.yaml`, `llm-api-proxy.yaml` |

- `external-mounts.yaml` is special-cased: creates a minimal empty config (not a copy of the `.example`, which contains sample paths that would fail mount validation)
- `user_secrets.yaml` is a per-user template, not a system config — excluded from validation
- `install.sh` creates all 5 configs during initial setup; subsequent `./run.sh build` calls validate silently

**To add a new config file**: add an entry to `CONFIG_REGISTRY` in `run.sh` and create a matching `.example` file in `config/`.

**CRITICAL**: Never delete, move, or overwrite `config/*.yaml` files during development, testing, or debugging. They are instance-specific, may contain credentials (`secrets.yaml`), and are not recoverable from git. To test config validation behavior, use a temporary directory or mock.

---

## Agent (`config/agent.yaml`)

```yaml
default_model: claude-sonnet-4-20250514
max_turns: 100
timeout_seconds: 1800
role: default                     # from prompts/roles/
```

---

## Deployment Mode (`AG3NTUM_MODE`)

Controls whether the web container serves a pre-built static bundle (production) or runs a Vite dev server (development).

| Value | Set by | Behavior |
|-------|--------|----------|
| `prod` (default) | `./run.sh build` | Static bundle from `/web_dist`, no npm install at runtime |
| `dev` | `./run.sh build --dev` | Vite dev server with HMR, npm install on startup |

Persisted in `.env` file as `AG3NTUM_MODE=prod` or `AG3NTUM_MODE=dev`. Survives `restart` calls.

**Switching modes**: Run `./run.sh build --dev` or `./run.sh build` to switch. A `restart` does NOT change modes — it uses the persisted value.

**`install.sh` defaults**: Production mode from `release` branch. Use `install.sh --dev` for development mode from `main` branch.

---

## User Config

- `user_requirements.txt` — pip packages for sandbox
- `user_secrets.yaml` — per-user encrypted credentials
- `subagents.yaml` — subagent models, tools, prompts
- `llm-api-proxy.yaml` — custom LLM routing, **auto-routes** models via proxy (@`how-to-connect-custom-llm.md`)
- `prompt-overrides.yaml` — allowlist for user-customizable prompts (execution, context, output, roles)

---

## Users

`./run.sh create-user` / `delete-user` / `cleanup-test-users`
Stored: `data/ag3ntum.db` → `users` table, `linux_uid` for sandbox isolation.

---

## External Mounts

Two-part: Docker volumes (build-time) + symlink auth (session-level). Read @`external_mounts.md`.

| Change | Command |
|--------|---------|
| Add/remove/change path | `./run.sh build` |
| Change user auth list | New session only |

---

## Secrets (`config/secrets.yaml`)

```yaml
ANTHROPIC_API_KEY: "sk-ant-..."
sandboxed_envs:               # Per-user, sandbox-only
  OPENAI_API_KEY: "sk-..."
```
