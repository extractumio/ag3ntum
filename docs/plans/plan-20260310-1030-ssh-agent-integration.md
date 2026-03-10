# SSH Agent Integration — Implementation Plan

## Context

SSH profile management (CRUD, vault-encrypted keys, connection testing) is fully built and working via UI/API. Three MCP tools (SSHExec, SSHRead, SSHConnect) are implemented and tested. The missing piece is wiring these profiles into the AI agent runtime so the agent can use SSH connections during chat sessions to manage remote servers.

## Goals & Non-Goals

- **Goal:** Users can create SSH profiles in the UI, then use them in agent chat to manage servers via natural language
- **Goal:** Agent auto-discovers configured profiles and includes them in its context
- **Goal:** Single-profile auto-selection for the common case (one server)
- **Goal:** Readonly mode (L0) works with zero friction; operations mode (L1) requires dry-run confirmation
- **Goal:** Session-scoped connections with keepalive and automatic cleanup
- **Non-goal:** Filtered shell mode (Phase 2 — needs additional security review)
- **Non-goal:** Streaming output (`tail -f`, `top` — not supported by SSHExec design)
- **Non-goal:** Frontend changes beyond what the chat already renders
- **Non-goal:** Multi-server parallel execution (Phase 2)

## Panel Assessment

Six experts analyzed the integration. Key insights:

- **Product/CTO:** Auto-select when one profile exists, confirm before first use. Show commands before execution for operations mode. This is the feature that makes SSH profiles useful.
- **Security:** Split approval by privilege level — L0 readonly needs no gate, L1+ operations shows dry-run first. Audit every command. Output redaction before LLM sees results. JWT is sufficient auth (no re-auth needed).
- **Backend:** Global connection pool singleton at API startup, per-session context creation. `close_session_connections()` exists but is never called — must wire into cleanup. DB session factory needed for vault key retrieval at runtime.
- **SysAdmin:** Truncate output at 32KB. Teach agent to use bounded commands (`tail -n`, `journalctl -n`, `--no-pager`). Command timeout should be configurable (10s is too low for some operations).
- **Frontend:** No Phase 1 changes needed — chat already renders tool outputs correctly.

## Recommended Approach

Create an `SSHServiceManager` singleton initialized at API startup that holds shared resources (connection pool, security config, command filter). When a session starts, load the user's active SSH profiles from DB, convert them to `SSHProfile` dataclasses, and build an `SSHToolContext`. Pass this context to `create_ag3ntum_tools_mcp_server()` which already supports it. Inject profile metadata into the system prompt so the agent knows what's available. Clean up connections when the session ends.

## Architecture

```
API Startup
  └─ SSHServiceManager (singleton)
       ├─ SSHConnectionPool (global, session-keyed)
       ├─ SSHSecurityConfig (from ssh-security.yaml)
       ├─ SSHCommandFilter (from ssh-privilege-levels.yaml)
       └─ SSHAuditService (shared)

Per-Session (in agent_core._execute)
  ├─ Load user's active SSH profiles from DB
  ├─ Convert SSHProfileRecord → SSHProfile via profile_to_ssh_profile()
  ├─ Create SSHCredentialVault (needs VaultService + VaultEncryption)
  ├─ Build SSHToolContext with shared services + user profiles
  ├─ Pass ssh_context to create_ag3ntum_tools_mcp_server()
  ├─ Inject SSH profile summary into system prompt
  └─ On session end: pool.close_session_connections(session_id)
```

## Implementation Steps

### Phase 1: Core Wiring (4 files to change, 1 new file, 1 new prompt)

#### Step 1: SSHServiceManager singleton

**New file:** `src/services/ssh_service_manager.py`

Create a manager that initializes shared SSH services at API startup:

```python
class SSHServiceManager:
    """Manages shared SSH infrastructure across all sessions."""

    def __init__(self):
        self._pool: SSHConnectionPool | None = None
        self._security_config: SSHSecurityConfig | None = None
        self._command_filter: SSHCommandFilter | None = None
        self._audit_service: SSHAuditService | None = None
        self._enabled: bool = False

    async def initialize(self) -> None:
        """Load config and create shared services. Called once at API startup."""
        self._security_config = load_ssh_security_config()
        if not self._security_config.enabled:
            return
        self._enabled = True
        self._pool = SSHConnectionPool(
            idle_timeout_seconds=self._security_config.idle_timeout,
            max_connections_per_session=self._security_config.max_connections,
        )
        self._command_filter = SSHCommandFilter(...)
        self._audit_service = SSHAuditService(...)

    async def build_session_context(
        self, session_id, user_id, profiles, db_session_factory, vault_service,
    ) -> SSHToolContext | None:
        """Build per-session SSH context. Returns None if SSH disabled or no profiles."""
        if not self._enabled or not profiles:
            return None
        credential_vault = SSHCredentialVault(vault_service)
        return SSHToolContext(
            session_id=session_id,
            user_id=user_id,
            security_config=self._security_config,
            connection_pool=self._pool,
            command_filter=self._command_filter,
            credential_vault=credential_vault,
            audit_service=self._audit_service,
            profiles=profiles,
            db_session_factory=db_session_factory,
            command_semaphore=asyncio.Semaphore(
                self._security_config.max_concurrent_commands,
            ),
        )

    async def cleanup_session(self, session_id: str) -> None:
        """Close all SSH connections for a session."""
        if self._pool:
            await self._pool.close_session_connections(session_id)

    async def shutdown(self) -> None:
        """Shutdown all SSH services. Called on API shutdown."""
        if self._pool:
            await self._pool.shutdown()
```

#### Step 2: Initialize SSHServiceManager at API startup

**File:** `src/api/main.py`

In `create_app()` lifespan or startup event:

```python
from src.services.ssh_service_manager import SSHServiceManager

ssh_manager = SSHServiceManager()

@asynccontextmanager
async def lifespan(app):
    await ssh_manager.initialize()
    app.state.ssh_manager = ssh_manager
    yield
    await ssh_manager.shutdown()
```

#### Step 3: Load user profiles and build SSH context in agent_core

**File:** `src/core/agent_core.py`

In `_execute()`, before `_build_options()` call (~line 1620):

```python
# Build SSH context if SSH is enabled and user has profiles
ssh_context = None
ssh_manager = getattr(app_state, 'ssh_manager', None)  # Or import singleton
if ssh_manager and ssh_manager.enabled:
    # Load user's active profiles from DB
    async with db_session_factory() as db:
        from src.services.ssh_profile_service import get_profiles, profile_to_ssh_profile
        records = await get_profiles(db, user_id)
        active_records = [r for r in records if r.is_active]
        if active_records:
            profiles = {
                r.name: profile_to_ssh_profile(r)
                for r in active_records
            }
            ssh_context = await ssh_manager.build_session_context(
                session_id=session_id,
                user_id=user_id,
                profiles=profiles,
                db_session_factory=db_session_factory,
                vault_service=vault_service,
            )
```

Pass `ssh_context` to `_build_options()` and then to `create_ag3ntum_tools_mcp_server()`.

In `_build_options()`, update the MCP server creation:

```python
ag3ntum_server = create_ag3ntum_tools_mcp_server(
    session_id=session_id,
    workspace_path=workspace_dir,
    sandbox_executor=sandbox_executor,
    include_bash=include_bash,
    ssh_context=ssh_context,  # NEW
    server_name="ag3ntum"
)
```

Add SSH tool names to `ag3ntum_tool_names`:

```python
if ssh_context is not None:
    ag3ntum_tool_names.extend([
        "mcp__ag3ntum__SSHExec",
        "mcp__ag3ntum__SSHRead",
        "mcp__ag3ntum__SSHConnect",
    ])
```

#### Step 4: Add SSH cleanup to session end

**File:** `src/core/agent_core.py`

In `_cleanup_session()`:

```python
# Close SSH connections for this session
if hasattr(self, '_ssh_manager') and self._ssh_manager:
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(self._ssh_manager.cleanup_session(session_id))
        else:
            loop.run_until_complete(self._ssh_manager.cleanup_session(session_id))
    except Exception as e:
        logger.warning("Failed to close SSH connections for %s: %s", session_id, e)
```

#### Step 5: SSH system prompt

**New file:** `prompts/system-prompts/07-ssh.md`

```markdown
---
variables:
  - SSH_PROFILES_BLOCK
  - AG3NTUM_SSH_EXEC_TOOL
  - AG3NTUM_SSH_READ_TOOL
  - AG3NTUM_SSH_CONNECT_TOOL
condition: SSH_ENABLED
---

## SSH Remote Server Access

You have access to SSH tools for managing remote servers. Available profiles:

${SSH_PROFILES_BLOCK}

### Tools

- **${AG3NTUM_SSH_EXEC_TOOL}**: Execute a command on a remote server.
  Parameters: `profile_name` (required), `command` (required), `dry_run` (optional, default false)

- **${AG3NTUM_SSH_READ_TOOL}**: Read a file from a remote server via SFTP.
  Parameters: `profile_name` (required), `path` (required)

- **${AG3NTUM_SSH_CONNECT_TOOL}**: Manage SSH connections.
  Parameters: `action` (required: list|connect|disconnect|status)

### Rules

1. **Profile selection**: If only one profile is configured, use it automatically.
   If multiple profiles exist, ask the user which one to use before the first command.
   Always confirm the profile name before the first SSH operation in a session.

2. **Readonly mode (L0)**: You may freely execute read-only commands (ls, cat, tail,
   grep, df, ps, systemctl status, journalctl). Use bounded output: prefer `tail -n 50`
   over `cat`, use `--no-pager` flags, use `-n` limits on journalctl.

3. **Operations mode (L1+)**: Before executing any write/modify command, show the user
   the exact command you plan to run and wait for confirmation. Use dry_run=true first
   when available (e.g., systemctl, apt).

4. **Output limits**: Command output may be truncated at 32KB. If you need more,
   use grep/awk to filter on the server side rather than fetching everything.

5. **Error handling**: If an SSH command fails, report the error clearly. Do not retry
   failed commands without telling the user. Connection errors may indicate the server
   is down or the profile is misconfigured.

6. **Never** expose SSH private keys, passphrases, or vault contents in your responses.
```

#### Step 6: Inject SSH profile info into prompt context

**File:** `src/core/agent_core.py` (prompt building section)

When building the prompt context, add SSH variables:

```python
if ssh_context:
    # Build profile summary block
    lines = []
    for name, profile in ssh_context.profiles.items():
        mode_label = {"readonly": "L0 readonly", "operations": "L1 operations",
                      "filtered_shell": "L2 filtered"}
        lines.append(
            f"- **{name}**: {profile.username}@{profile.host}:{profile.port} "
            f"({mode_label.get(profile.mode, profile.mode)})"
            + (f" — {profile.description}" if profile.description else "")
        )
    prompt_context.strings["SSH_PROFILES_BLOCK"] = "\n".join(lines)
    prompt_context.flags["SSH_ENABLED"] = True
    prompt_context.strings["AG3NTUM_SSH_EXEC_TOOL"] = "mcp__ag3ntum__SSHExec"
    prompt_context.strings["AG3NTUM_SSH_READ_TOOL"] = "mcp__ag3ntum__SSHRead"
    prompt_context.strings["AG3NTUM_SSH_CONNECT_TOOL"] = "mcp__ag3ntum__SSHConnect"
else:
    prompt_context.flags["SSH_ENABLED"] = False
```

### Phase 1 Summary: Files to Change

| File | Change |
|------|--------|
| `src/services/ssh_service_manager.py` | **NEW** — singleton managing shared SSH services |
| `src/api/main.py` | Initialize SSHServiceManager at startup, shutdown on exit |
| `src/core/agent_core.py` | Load profiles, build SSH context, pass to MCP server, add tool names, cleanup on session end |
| `prompts/system-prompts/07-ssh.md` | **NEW** — SSH system prompt with profile injection |
| `src/core/agent_core.py` | Inject SSH profile info into prompt context |

### Phase 2 (Future)

- Filtered shell mode (L2) after security review
- Frontend SSH session indicator in chat header
- Multi-server parallel execution
- SSH command history/audit view in UI
- Streaming output for long-running commands
- Profile selector widget in chat input area

## Key Decisions

| Decision | Chosen | Rationale | Alternatives Considered |
|----------|--------|-----------|------------------------|
| Connection pool scope | Global singleton, session-keyed | Efficient resource sharing; existing pool already isolates by `session_id:profile_name` | Per-user pool (wasteful), per-session pool (no connection reuse) |
| Approval gate | Split by mode: L0=none, L1+=dry-run | Balances security with UX. Readonly is safe; operations need human confirmation | Always require approval (too much friction), never require (too risky) |
| Profile auto-selection | Auto-select single profile with confirmation message | 80% of users will have 1-2 profiles. Explicit naming for multi-profile | Always require explicit name (poor UX), silent auto-select (confusing) |
| Authentication | JWT sufficient, no re-auth | User already authenticated. Profile creation itself required key upload | Password re-prompt (friction, no security benefit) |
| Output truncation | 32KB with truncation marker | Protects LLM context budget. SysAdmin recommended bounded commands in prompt | No limit (context overflow), 10KB (too restrictive) |
| Filtered shell | Excluded from Phase 1 | Allows arbitrary commands within filter — needs more testing | Include with extra warnings (too risky for v1) |
| Prompt injection | Conditional prompt file with `condition: SSH_ENABLED` flag | Clean separation. No SSH noise in prompt when disabled. Uses existing template engine | Hardcoded in agent_core (messy), separate MCP prompt (over-engineered) |

## Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| Prompt injection causing unintended SSH commands | High | Command filter enforces allowlist per privilege level. L1+ requires dry-run confirmation. Output redaction strips sensitive data. |
| Key material in error messages | High | SSHCredentialVault never returns raw keys to tool responses. Error messages are generic ("authentication failed"). |
| Stale connections after session crash | Medium | Connection pool has 15-min idle timeout + health checker. `close_session_connections()` called in all cleanup paths. |
| Large command output overwhelming LLM | Medium | 32KB truncation enforced in SSHExec. Prompt teaches agent to use bounded commands. |
| Wrong profile selected | Low | Agent confirms profile before first use. Multiple profiles require explicit naming. |
| SSH security config missing | Low | Fail-closed: `load_ssh_security_config()` defaults to `enabled=False`. No SSH tools registered if disabled. |
| DB session lifecycle mismatch | Medium | SSH tools use their own DB session factory (independent of agent's main session). |
| Connection pool exhaustion | Low | `max_connections_per_session=5` limit. Idle timeout reclaims unused connections. |

## Open Questions

- [ ] Command timeout: Is 10s sufficient? Should it be configurable per-profile or per-command? (SysAdmin recommends 30s default with override)
- [ ] Audit UI: Should Phase 1 include a basic audit log view, or is backend logging sufficient? (CTO wants to see what the agent ran)
- [ ] Subagent SSH access: Should subagents inherit SSH tools, or only the main agent? (Security recommends main agent only)
