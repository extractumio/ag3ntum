# How to Debug Agent with ag3ntum_debug.py

Complete guide for debugging Ag3ntum agent execution using the debug CLI script.

---

## Prerequisites

### 1. Docker Container Running

The API must be running in Docker. Check status:

```bash
docker ps | grep ag3ntum
```

Expected output:
```
project-ag3ntum-api-1   Up X minutes   0.0.0.0:40080->40080/tcp
project-ag3ntum-web-1   Up X minutes   0.0.0.0:50080->50080/tcp
```

If not running, start with:
```bash
cd Project
./deploy.sh build
```

### 2. Authentication Credentials

You need valid user credentials. The default test user is:
- **Email**: `...` (e.g. test@anthropic.com)
- **Password**: `...` 
- **Username**: `...` (Note: it is used for file paths, not auth, e.g. /users/anthropic_user_001/, not test@anthropic.com...!)

**Note**: Authentication uses email, but file artifacts are stored under the username.

### 3. Python Environment

Activate the virtual environment:
```bash
cd Project
source venv/bin/activate
```

---

## Quick Start

### Basic Usage

```bash
# Simple request
python scripts/ag3ntum_debug.py -r "who are you?" \
  --email "info@extractum.io" --password bzzzzzzzz247824_

# File creation task
python scripts/ag3ntum_debug.py -r "create a python script to print HELLO WORLD" \
  --email "info@extractum.io" --password bzzzzzzzz247824_

# With verbose output (shows all events)
python scripts/ag3ntum_debug.py -r "list files in workspace" \
  --email "info@extractum.io" --password bzzzzzzzz247824_ --verbose

# Security-focused (shows only blocked operations)
python scripts/ag3ntum_debug.py -r "read /etc/passwd" \
  --email "info@extractum.io" --password bzzzzzzzz247824_ --security-only

# Dump session files after execution
python scripts/ag3ntum_debug.py -r "create test.txt" \
  --email "info@extractum.io" --password bzzzzzzzz247824_ --dump-session
```

### Command-Line Options

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--request` | `-r` | Request to send to agent | **(required)** |
| `--email` | `-e` | User email for authentication | **(required)** |
| `--password` | | Password for authentication | `test123` |
| `--host` | | API host | `localhost` |
| `--port` | `-p` | API port | `40080` |
| `--verbose` | `-v` | Show all SSE events | `false` |
| `--security-only` | `-s` | Show only security events | `false` |
| `--dump-session` | `-d` | Dump session files after run | `false` |

---

## Finding Artifacts After Execution

### Session Directory Structure

After a successful run, the script outputs:

```
Session Path: users/greg/sessions/20260110_190812_93bbdebe
```

**Important**: The path shown uses the email, but the actual filesystem path uses the username!

Correct path formula:
```
Project/users/{USERNAME}/sessions/{SESSION_ID}/
```

Example:
```bash
# Shown by script (now displays correct username)
Session Path: users/greg/sessions/20260110_190812_93bbdebe

# Actual filesystem location (matches script output)
cd Project/users/greg/sessions/20260110_190812_93bbdebe/
```

### Session Directory Contents

```
20260110_190812_93bbdebe/
├── agent.jsonl          # Detailed event log (all SDK events)
├── trace.json           # Execution trace
├── output.md            # Agent's final output
├── workspace/           # Files created/modified by agent
│   └── hello_world.py   # Example: created file
└── checkpoints/         # File version history
```

### Key Files

#### 1. `workspace/` - Agent's Working Directory

All files created or modified by the agent:

```bash
ls -la Project/users/greg/sessions/{SESSION_ID}/workspace/
```

This is mapped to `/workspace` inside the agent's sandbox.

#### 2. `agent.jsonl` - Complete Event Log

Line-delimited JSON with all SDK events:

```bash
# View tool calls
cat agent.jsonl | grep '"tool_use_id"' | head -10

# Extract tool execution results
cat agent.jsonl | jq -r 'select(.content) | .content[0]'

# Find errors
cat agent.jsonl | grep -i "error\|denied\|blocked"
```

#### 3. `output.md` - Agent's Final Response

The agent's final message to the user:

```bash
cat Project/users/greg/sessions/{SESSION_ID}/output.md
```

---

## Common Issues and Fixes

### Issue 1: "Internal error: create_permission_callback() got an unexpected keyword argument 'sandbox_executor'"

**Symptom**: Agent fails immediately with this error.

**Cause**: `agent_core.py` was passing `sandbox_executor` parameter that `create_permission_callback()` doesn't accept.

**Fix Applied** (2026-01-10):
```python
# File: Project/src/core/agent_core.py:512-519

# BEFORE (broken):
can_use_tool = create_permission_callback(
    permission_manager=self._permission_manager,
    on_permission_check=on_permission_check,
    denial_tracker=self._denial_tracker,
    trace_processor=trace_processor,
    system_message_builder=self._sandbox_system_message_builder,
    sandbox_executor=sandbox_executor,  # ❌ This parameter doesn't exist
)

# AFTER (fixed):
can_use_tool = create_permission_callback(
    permission_manager=self._permission_manager,
    on_permission_check=on_permission_check,
    denial_tracker=self._denial_tracker,
    trace_processor=trace_processor,
    system_message_builder=self._sandbox_system_message_builder,
    # ✅ sandbox_executor parameter removed
)
```

**Resolution**: Removed the `sandbox_executor` parameter from the function call.

---

### Issue 2: Write Tool Denied - "No mcp__ag3ntum_write__Write operations are allowed"

**Symptom**: 
- Agent completes successfully
- No tool calls shown in output
- Agent responds: "I'm unable to create the Python script because write permissions are not available"
- Session logs show: `"mcp__ag3ntum_write__Write for './hello_world.py' is not permitted"`

**Cause**: Incorrect MCP tool name patterns in `permissions.yaml` AND inconsistent MCP server architecture.

**Root Cause**:
The file tools were being created as separate MCP servers (one per tool), which generated inconsistent tool names with server names embedded in them:
```python
# OLD ARCHITECTURE (WRONG):
# Each tool got its own MCP server:
mcp_servers["ag3ntum_read"] = create_ag3ntum_read_mcp_server(session_id, "ag3ntum_read")
mcp_servers["ag3ntum_write"] = create_ag3ntum_write_mcp_server(session_id, "ag3ntum_write")
# etc.

# This generated tool names like:
# mcp__ag3ntum_read__Read  (server name embedded)
# mcp__ag3ntum_write__Write
# etc.
```

This created a naming inconsistency where:
- `tools.enabled` section used: `mcp__ag3ntum__Read` 
- `allow` rules needed to match actual names: `mcp__ag3ntum_read__Read` (with server name)
- Comments said: "Use mcp__ag3ntum__Read" (without server name)

**Fix Applied** (2026-01-10):

1. **Created unified MCP server** (`ag3ntum_file_tools.py`):
```python
# UNIFIED ARCHITECTURE:
# All Ag3ntum tools (Bash + file tools) in ONE MCP server named "ag3ntum":
def create_ag3ntum_tools_mcp_server(
    session_id, workspace_path, sandbox_executor, 
    include_bash=True, server_name="ag3ntum"
):
    tools = [
        create_bash_tool(workspace_path, sandbox_executor),  # If include_bash
        create_read_tool(session_id),
        create_write_tool(session_id),
        # ... all 9 tools total
    ]
    return create_sdk_mcp_server(name=server_name, version="1.0.0", tools=tools)
```

2. **Updated `agent_core.py`** to use unified server:
```python
# BEFORE (broken):
# Separate servers with inconsistent naming
bash_mcp_server = create_ag3ntum_bash_mcp_server(...)
mcp_servers["ag3ntum"] = bash_mcp_server
file_tools_server = create_ag3ntum_file_tools_mcp_server(...)
mcp_servers["ag3ntum"] = file_tools_server  # Overwrites Bash!

# AFTER (fixed):
# Single unified server with all tools
ag3ntum_server = create_ag3ntum_tools_mcp_server(
    session_id=session_id,
    workspace_path=workspace_dir,
    sandbox_executor=sandbox_executor,
    include_bash=True,
    server_name="ag3ntum"
)
mcp_servers["ag3ntum"] = ag3ntum_server
```

3. **Updated `permissions.yaml`** to match consistent names:
```yaml
# All tool names now consistent: mcp__ag3ntum__ToolName
allow:
  - mcp__ag3ntum__Bash(*)
  - mcp__ag3ntum__Read(*)         # ✅ Consistent
  - mcp__ag3ntum__Write(*)        # ✅ Consistent
  - mcp__ag3ntum__Edit(*)         # ✅ Consistent
  - mcp__ag3ntum__MultiEdit(*)    # ✅ Consistent
  - mcp__ag3ntum__Glob(*)         # ✅ Consistent
  - mcp__ag3ntum__Grep(*)         # ✅ Consistent
  - mcp__ag3ntum__LS(*)           # ✅ Consistent
  - mcp__ag3ntum__WebFetch(*)     # ✅ Consistent
```

**How to Diagnose**:

1. Check session logs for exact tool name:
```bash
cat users/greg/sessions/{SESSION_ID}/agent.jsonl | grep '"tool_use_id"'
```

Look for error messages like:
```json
{"tool_use_id": "...", "content": "mcp__ag3ntum_write__Write for '...' is not permitted", "is_error": true}
```

2. Compare tool name against permissions.yaml patterns.

**Resolution**: Updated all MCP tool patterns to include the server name separator (`_servername_`).

---

### Issue 3: Authentication Failed (404)

**Symptom**: `Auth failed: 404 - {"detail":"Not Found"}`

**Cause**: Wrong endpoint. The script was using `/auth/token` but the API endpoint is `/auth/login`.

**Fix Applied** (2026-01-10):
```python
# File: Project/scripts/ag3ntum_debug.py:89-95

# BEFORE:
resp = await client.post(
    f"{self.api_url}/auth/token",     # ❌ Wrong endpoint
    data={"username": username, "password": password},  # ❌ Wrong format
)

# AFTER:
resp = await client.post(
    f"{self.api_url}/auth/login",     # ✅ Correct endpoint
    json={"email": username, "password": password},     # ✅ Correct format (JSON, email)
)
```

**Resolution**: Updated authentication endpoint and request format.

---

## Debugging Workflow

### Step 1: Run Simple Test

Start with a basic request to verify the system is working:

```bash
python scripts/ag3ntum_debug.py -r "who are you?" \
  --email "head@extractum.io" --password sde45f
```

Expected output:
```
✓ Authenticated as head@extractum.io
✓ Session: 20260110_190252_f6d64f4e
Streaming events...

╭─────────────── Summary ───────────────╮
│ Status: agent_complete                │
│ Duration: 6597ms                      │
│ Tool Calls: 0 (0 allowed, 0 blocked)  │
╰───────────────────────────────────────╯
```

### Step 2: Test File Creation

Verify tools are working:

```bash
python scripts/ag3ntum_debug.py -r "create a python script to print HELLO WORLD" \
  --email "head@extractum.io" --password sde45f
```

Expected: File created in workspace.

### Step 3: Inspect Artifacts

```bash
# Find latest session
ls -lt Project/users/greg/sessions/ | head -5

# Check workspace
ls -la Project/users/greg/sessions/{SESSION_ID}/workspace/

# View created file
cat Project/users/greg/sessions/{SESSION_ID}/workspace/hello_world.py

# Check for errors
cat Project/users/greg/sessions/{SESSION_ID}/agent.jsonl | grep -i "error"
```

### Step 4: Deep Dive (if issues)

```bash
# Run with verbose output
python scripts/ag3ntum_debug.py -r "your request" \
  --email "head@extractum.io" --password sde45f --verbose

# Check permission logs in Docker
docker logs project-ag3ntum-api-1 2>&1 | grep -i "permission\|denied" | tail -30

# Inspect full event log
cat Project/users/greg/sessions/{SESSION_ID}/agent.jsonl | jq '.'
```

---

## Advanced Diagnostics

### Check Exact Tool Names

To see what tool names are actually being sent:

```bash
# In session log
cat agent.jsonl | jq -r 'select(.event.type=="content_block_start") | .event.content_block.name' | sort -u
```

Example output:
```
mcp__ag3ntum_write__Write
mcp__ag3ntum_read__Read
mcp__ag3ntum__Bash
```

### View Permission Checks

```bash
# Enable debug logging in Docker
docker logs project-ag3ntum-api-1 2>&1 | grep "PERMISSION_CHECK"
```

Example output:
```
PERMISSION_CHECK: Checking tool_call='mcp__ag3ntum_write__Write(./hello.py)'
PERMISSION_CHECK: Tool 'mcp__ag3ntum_write__Write(./hello.py)' ALLOWED by pattern 'mcp__ag3ntum_write__Write(*)'
```

### Trace Tool Execution

```bash
# View all tool starts and completions
cat agent.jsonl | jq -r 'select(.type=="tool_start" or .type=="tool_complete") | "\(.type): \(.data.name)"'
```

---

## Testing Security

### Test Blocked Operations

```bash
# Try reading outside workspace
python scripts/ag3ntum_debug.py -r "read /etc/passwd" \
  --email "head@extractum.io" --password sde45f --security-only

# Try dangerous commands
python scripts/ag3ntum_debug.py -r "run: rm -rf /" \
  --email "head@extractum.io" --password sde45f --security-only
```

Expected: Operations should be blocked with clear denial messages.

---

## Exit Codes

The script returns different exit codes based on execution status:

| Code | Meaning | Example |
|------|---------|---------|
| `0` | Success - agent completed | Normal task completion |
| `1` | Error - authentication or execution failed | Connection error, task error |
| `2` | Security blocks - operations were denied | Permission violations |

Use in scripts:
```bash
python scripts/ag3ntum_debug.py -r "test" --email "head@extractum.io" --password sde45f
if [ $? -eq 2 ]; then
    echo "Security blocks detected"
fi
```

---

## Quick Reference Card

```bash
# Prerequisites
docker ps | grep ag3ntum                    # Check Docker running
source venv/bin/activate                    # Activate Python env

# Run agent
python scripts/ag3ntum_debug.py -r "YOUR REQUEST" \
  --email "head@extractum.io" --password sde45f

# Find artifacts (replace SESSION_ID from output)
cd Project/users/greg/sessions/{SESSION_ID}/
ls -la workspace/                           # Created files
cat output.md                               # Agent response
cat agent.jsonl | grep "error"              # Check errors

# Common issues
docker logs project-ag3ntum-api-1 --tail 50  # Check Docker logs
cat agent.jsonl | grep '"tool_use_id"'      # Check tool execution
```

---

## Checklist for Debugging

Before running:
- [ ] Docker container is running (`docker ps`)
- [ ] Virtual environment activated (`source venv/bin/activate`)
- [ ] Have correct credentials (email + password)

After running:
- [ ] Check exit code ($?)
- [ ] Note the Session ID from output
- [ ] Find artifacts in `users/{username}/sessions/{session_id}/`
- [ ] Check `workspace/` for created files
- [ ] Review `agent.jsonl` for errors
- [ ] Check Docker logs if internal errors occur

---

## Summary of Fixes (2026-01-10)

1. **Fixed `agent_core.py`**: Removed invalid `sandbox_executor` parameter from `create_permission_callback()` call
2. **Fixed tool naming and Bash availability**: 
   - Created unified MCP server (`ag3ntum_file_tools.py`) combining ALL tools (Bash + 8 file tools)
   - Updated `agent_core.py` to use single "ag3ntum" server for all tools
   - This ensures consistent tool naming: `mcp__ag3ntum__Bash`, `mcp__ag3ntum__Read`, `mcp__ag3ntum__Write`, etc.
   - Bash tool is now available (was missing due to server name collision)
3. **Fixed `permissions.yaml`**: Updated to use consistent tool name patterns (matching tools.enabled section)
4. **Fixed `ag3ntum_debug.py`**: Changed auth endpoint from `/auth/token` to `/auth/login` with JSON body

All fixes were deployed and verified working.
