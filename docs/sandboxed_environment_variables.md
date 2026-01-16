# Sandboxed Environment Variables

**Last Updated:** 2026-01-16
**Project:** Ag3ntum Agent Framework

---

## Overview

Ag3ntum supports **sandboxed environment variables** that allow API keys and secrets to be securely passed to the Bubblewrap sandbox for use by agent commands. These variables are:

1. **Isolated** - Only available inside the sandbox (via `Ag3ntumBash` commands)
2. **Session-scoped** - Dynamically loaded per session based on user context
3. **Layered** - Global configuration with user-specific overrides

This feature enables agents to access external services (like Gemini API, OpenAI API, etc.) without exposing secrets to the main Python process or other users.

---

## Architecture

### Security Model

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SANDBOXED ENVIRONMENT VARIABLES                          │
└─────────────────────────────────────────────────────────────────────────────┘

                         Secrets Loading Flow

┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│   1. Global Secrets (config/secrets.yaml)                                   │
│      └─ sandboxed_envs:                                                     │
│            OPENAI_API_KEY: sk-global-key                                    │
│            SHARED_VAR: global_value                                         │
│                                                                              │
│                         ▼ (merge with user overrides)                        │
│                                                                              │
│   2. User Secrets (/users/{username}/ag3ntum/secrets.yaml)                  │
│      └─ sandboxed_envs:                                                     │
│            OPENAI_API_KEY: sk-user-specific-key  ← Overrides global         │
│            USER_SECRET: user_only_value          ← Added                    │
│                                                                              │
│                         ▼ (merged result)                                    │
│                                                                              │
│   3. Final Sandboxed Envs:                                                  │
│      ├─ OPENAI_API_KEY: sk-user-specific-key (from user)                    │
│      ├─ SHARED_VAR: global_value (from global)                              │
│      └─ USER_SECRET: user_only_value (from user)                            │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘

                         Injection into Sandbox

┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│   4. SandboxConfig.environment.custom_env = merged_envs                     │
│                                                                              │
│   5. SandboxExecutor.build_bwrap_command():                                 │
│      bwrap --clearenv \                                                     │
│            --setenv HOME /workspace \                                       │
│            --setenv PATH /usr/bin:/bin \                                    │
│            --setenv OPENAI_API_KEY sk-user-specific-key \    ← Custom envs  │
│            --setenv SHARED_VAR global_value \                               │
│            --setenv USER_SECRET user_only_value \                           │
│            ... \                                                             │
│            -- bash -c "command"                                              │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Key Security Properties

| Property | Description |
|----------|-------------|
| **Sandbox-only access** | Environment variables are only visible inside the Bubblewrap sandbox |
| **Process isolation** | The main Python process (API, agent) never sees these secrets |
| **User isolation** | User A's secrets are not visible to User B's sessions |
| **Session scope** | Secrets are loaded fresh for each session |
| **No inheritance** | `--clearenv` ensures NO host environment leaks into sandbox |

---

## Configuration

### Global Secrets (`config/secrets.yaml`)

This file contains secrets available to ALL users unless overridden:

```yaml
# API key for Anthropic (used by main agent process)
anthropic_api_key: sk-ant-your-key-here

# Sandboxed environment variables - passed to Bubblewrap sandbox
# These are available inside Ag3ntumBash commands but NOT in main process
sandboxed_envs:
  # Example: API keys for external services
  GEMINI_API_KEY: AIzaSyA9fS1CIxzQbJZnh0NHhIzyJwVrrDpl68s
  OPENAI_API_KEY: sk-global-default-key

  # Example: Database connection strings
  DATABASE_URL: postgres://user:pass@localhost/db

  # Example: Any custom environment variable
  MY_SERVICE_TOKEN: token-value-here
```

### User-Specific Secrets (`/users/{username}/ag3ntum/secrets.yaml`)

Users can override global secrets with their own values. This file is optional:

```yaml
# User-specific sandboxed environment variables
# These OVERRIDE global values for this user only
sandboxed_envs:
  # Override the global OPENAI_API_KEY with user's own key
  OPENAI_API_KEY: sk-user-specific-key

  # Add user-specific variables (not in global)
  GITHUB_TOKEN: ghp_user_personal_token
```

**File Location:**
- Inside Docker: `/users/{username}/ag3ntum/secrets.yaml`
- Host mapping: `./users/{username}/ag3ntum/secrets.yaml`

---

## How It Works

### 1. Session Creation Flow

When a session is created for a user, the following happens:

```python
# In agent_core.py _build_options()

# 1. Load merged sandboxed_envs (global + user overrides)
sandboxed_envs = load_sandboxed_envs(username=username)
# Returns: {"GEMINI_API_KEY": "...", "OPENAI_API_KEY": "...", ...}

# 2. Pass to permission manager when getting sandbox config
sandbox_config = self._permission_manager.get_sandbox_config(
    sandboxed_envs=sandboxed_envs
)
# Injects into: sandbox_config.environment.custom_env

# 3. SandboxExecutor receives the config with custom_env populated
sandbox_executor = self._build_sandbox_executor(sandbox_config, workspace_dir)
```

### 2. Command Execution Flow

When `Ag3ntumBash` executes a command:

```python
# In sandbox.py build_bwrap_command()

# After --clearenv (which removes ALL environment variables):
cmd.append("--clearenv")

# Standard environment variables
cmd.extend(["--setenv", "HOME", "/workspace"])
cmd.extend(["--setenv", "PATH", "/usr/bin:/bin"])

# Custom environment variables from sandboxed_envs
for env_name, env_value in config.environment.custom_env.items():
    cmd.extend(["--setenv", env_name, env_value])

# Result: Environment inside sandbox contains ONLY:
# - HOME=/workspace
# - PATH=/usr/bin:/bin
# - Plus all sandboxed_envs (GEMINI_API_KEY, OPENAI_API_KEY, etc.)
```

### 3. Inside the Sandbox

Commands executed via `mcp__ag3ntum__Bash` see the environment:

```bash
# Agent runs: mcp__ag3ntum__Bash(command="env | grep API")

# Inside sandbox, the command sees:
GEMINI_API_KEY=AIzaSyA9fS1CIxzQbJZnh0NHhIzyJwVrrDpl68s
OPENAI_API_KEY=sk-user-specific-key
```

---

## Usage Examples

### Example 1: Using Gemini API in Agent Scripts

**Global secrets.yaml:**
```yaml
sandboxed_envs:
  GEMINI_API_KEY: AIzaSyA9fS1CIxzQbJZnh0NHhIzyJwVrrDpl68s
```

**Agent task:**
```
Run this Python script to test Gemini API:
python test_gemini.py
```

**Inside test_gemini.py (created by agent):**
```python
import os
import google.generativeai as genai

# GEMINI_API_KEY is available from sandboxed_envs
api_key = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=api_key)

model = genai.GenerativeModel("gemini-pro")
response = model.generate_content("Hello, Gemini!")
print(response.text)
```

### Example 2: Per-User API Key Override

**Global secrets.yaml:**
```yaml
sandboxed_envs:
  OPENAI_API_KEY: sk-default-organization-key
```

**User greg's secrets (/users/greg/ag3ntum/secrets.yaml):**
```yaml
sandboxed_envs:
  OPENAI_API_KEY: sk-greg-personal-key
```

**Result:**
- Sessions for user `greg` use `sk-greg-personal-key`
- Sessions for other users use `sk-default-organization-key`

### Example 3: Verifying Environment in Sandbox

**Agent command:**
```bash
mcp__ag3ntum__Bash(command="env | sort")
```

**Output (inside sandbox):**
```
GEMINI_API_KEY=AIzaSyA9fS1CIxzQbJZnh0NHhIzyJwVrrDpl68s
HOME=/workspace
OPENAI_API_KEY=sk-user-specific-key
PATH=/usr/bin:/bin
```

Note: NO other environment variables from host/container are visible due to `--clearenv`.

### Example 4: Using Environment Variables in Skills

Skills can access sandboxed environment variables in two ways:

**Method 1: Instruction-based skills calling Bash**

Skills defined in SKILL.md files can instruct the agent to use `mcp__ag3ntum__Bash` to run scripts:

```markdown
# SKILL.md
name: gemini-test
description: Test Gemini API integration

## Instructions
To test Gemini API:
1. Create a Python script that uses the GEMINI_API_KEY environment variable
2. Run it using: mcp__ag3ntum__Bash(command="python /workspace/test_gemini.py")
```

When the agent runs scripts via `mcp__ag3ntum__Bash`, environment variables from `sandboxed_envs` are automatically available.

**Method 2: Script-based skills (MCP tools)**

Skills with associated scripts (`.py`, `.sh`, `.bash` files) are automatically registered as MCP tools:

```
.claude/skills/
└── my-skill/
    ├── SKILL.md
    └── scripts/
        └── run.py  # Automatically registered as mcp__skills__skill_my_skill
```

**run.py:**
```python
import os

# GEMINI_API_KEY is available when skill runs inside sandbox
api_key = os.environ.get("GEMINI_API_KEY")
if api_key:
    print(f"API key found: {api_key[:10]}...")
else:
    print("Warning: GEMINI_API_KEY not set")
```

The agent can invoke this as:
```
mcp__skills__skill_my_skill(args=[], input_data="")
```

**SECURITY: Script-based skills MUST run inside the Bubblewrap sandbox.**

The skills MCP server is only created when `SandboxExecutor` is available. If the sandbox is not configured, script-based skills will fail with an explicit error:

```
SECURITY ERROR: Cannot execute skill 'my-skill' - SandboxExecutor is not configured.
Script-based skills MUST run inside the Bubblewrap sandbox.
```

This fail-closed design ensures that skills never run in an insecure environment with potential access to secrets.

---

## Security Considerations

### What's Protected

1. **Process Isolation**: Main Python process (agent, API server) never sees sandboxed_envs
2. **User Isolation**: User A cannot access User B's sandboxed_envs
   - Each session gets a **fresh** `SandboxEnvConfig` instance
   - `SandboxConfig.resolve()` creates a new environment object per-session
   - User-specific envs from `/users/{username}/ag3ntum/secrets.yaml` are isolated
   - No shared state between sessions that could leak keys
3. **Container Escape**: Even if agent escapes sandbox, host secrets are not in container env
4. **Environment Leakage**: `--clearenv` prevents ANY host/container environment from leaking
5. **Cross-Session Leakage**: Prevented by creating fresh config objects per-session
   - `custom_env` starts empty for each session
   - Only the current user's sandboxed_envs are injected

### What's NOT Protected

1. **Inside Sandbox**: Agent commands CAN read these variables via `env`, `printenv`, etc.
2. **Workspace Files**: Agent could write secrets to files in workspace
3. **Network Exfiltration**: Agent could send secrets to external servers (if network enabled)

### Mitigation Strategies

| Risk | Mitigation |
|------|------------|
| Agent reads env | Expected behavior - that's the purpose |
| Agent writes to file | Monitor workspace files, use read-only skills |
| Network exfiltration | Use `network_sandboxing: true` or network blocklist |
| Logging exposure | Secrets are masked in bwrap command logs (`***`) |

---

## Implementation Details

### Files Modified

| File | Changes |
|------|---------|
| `config/secrets.yaml` | Added `sandboxed_envs` section |
| `src/config.py` | Added `get_sandboxed_envs()` method, `load_sandboxed_envs()` function |
| `src/core/sandbox.py` | Added `custom_env` field to `SandboxEnvConfig`, updated `build_bwrap_command()`, **SECURITY: `resolve()` creates fresh env per-session** |
| `src/core/permission_profiles.py` | Updated `get_sandbox_config()` to accept `sandboxed_envs` parameter, defensive empty check |
| `src/core/agent_core.py` | Updated `_build_options()` to load and inject sandboxed_envs, added skills MCP server |
| `src/core/skill_tools.py` | Added `sandbox_executor` (REQUIRED), sandbox is mandatory for skill execution |

### Key Functions

```python
# src/config.py
def load_sandboxed_envs(
    username: Optional[str] = None,
    config_loader: Optional[AgentConfigLoader] = None,
) -> dict[str, str]:
    """Load and merge sandboxed_envs from global + user-specific secrets."""

# src/core/permission_profiles.py
def get_sandbox_config(
    self,
    sandboxed_envs: Optional[dict[str, str]] = None,
) -> Optional[SandboxConfig]:
    """Resolve sandbox config with injected sandboxed_envs."""

# src/core/sandbox.py
def resolve(self, placeholders: dict[str, str]) -> SandboxConfig:
    """SECURITY: Creates fresh SandboxEnvConfig per-session to prevent leakage."""

def build_bwrap_command(...) -> list[str]:
    """Build bwrap command with custom_env applied via --setenv."""

# src/core/skill_tools.py
class SkillToolsManager:
    def __init__(
        self,
        sandbox_executor: Optional[SandboxExecutor] = None,  # REQUIRED
    ):
        """Manager for script-based skills. Sandbox is MANDATORY."""
```

---

## Logging

Sandboxed environment variable activity is logged at INFO level:

```
INFO: Loaded 2 global sandboxed_envs
INFO: Loaded 1 user-specific sandboxed_envs from /users/greg/ag3ntum/secrets.yaml
INFO: Merged sandboxed_envs: 2 global + 1 user = 3 total (after overrides)
INFO: SANDBOX: Loaded 3 sandboxed env vars for user 'greg': ['GEMINI_API_KEY', 'OPENAI_API_KEY', 'GITHUB_TOKEN']
INFO: Injected 3 sandboxed environment variables into sandbox config for session 20260116_123456_abc12345
INFO: BWRAP: Applied 3 custom env vars from sandboxed_envs
DEBUG: BWRAP: Set custom env GEMINI_API_KEY=***
DEBUG: BWRAP: Set custom env OPENAI_API_KEY=***
DEBUG: BWRAP: Set custom env GITHUB_TOKEN=***
```

---

## Testing

### Verify Environment in Sandbox

```bash
# Run through Ag3ntumBash
mcp__ag3ntum__Bash(command="env | grep -E 'GEMINI|OPENAI'")

# Expected output:
GEMINI_API_KEY=AIzaSyA9fS1CIxzQbJZnh0NHhIzyJwVrrDpl68s
OPENAI_API_KEY=sk-user-specific-key
```

### Verify User Override

1. Create user-specific secrets:
   ```bash
   mkdir -p /users/testuser/ag3ntum
   cat > /users/testuser/ag3ntum/secrets.yaml << EOF
   sandboxed_envs:
     OPENAI_API_KEY: sk-testuser-override
   EOF
   ```

2. Run session as `testuser` and check env:
   ```bash
   mcp__ag3ntum__Bash(command="echo $OPENAI_API_KEY")
   # Output: sk-testuser-override
   ```

---

## References

- [Layers of Security for Filesystem](./layers_of_security_for_filesystem.md) - Complete security architecture
- [Current Architecture](./current_architecture.md) - System architecture overview
- [Bubblewrap](https://github.com/containers/bubblewrap) - Sandboxing tool documentation
