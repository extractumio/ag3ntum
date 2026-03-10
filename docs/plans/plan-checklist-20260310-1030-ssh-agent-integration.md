# SSH Agent Integration — Verification Checklist

**Plan:** [plan-20260310-1030-ssh-agent-integration.md](plan-20260310-1030-ssh-agent-integration.md)

---

## Step 1: SSHServiceManager Singleton

- [ ] `src/services/ssh_service_manager.py` created
- [ ] `SSHServiceManager` class with `initialize()`, `build_session_context()`, `cleanup_session()`, `shutdown()`
- [ ] `initialize()` loads `ssh-security.yaml` via `load_ssh_security_config()`
- [ ] `initialize()` creates shared `SSHConnectionPool`, `SSHCommandFilter`, `SSHAuditService`
- [ ] `enabled` property returns `False` when SSH config is disabled or missing (fail-closed)
- [ ] `build_session_context()` returns `None` when disabled or no profiles
- [ ] `build_session_context()` creates `SSHCredentialVault` with provided `VaultService`
- [ ] `build_session_context()` returns properly populated `SSHToolContext`
- [ ] `cleanup_session()` calls `pool.close_session_connections(session_id)`
- [ ] `shutdown()` calls `pool.shutdown()`
- [ ] Unit tests for SSHServiceManager (enabled/disabled, no profiles, build context, cleanup)
- [ ] Lint clean: `flake8 src/services/ssh_service_manager.py --config=.flake8`

## Step 2: API Startup Integration

- [ ] `src/api/main.py` imports `SSHServiceManager`
- [ ] `SSHServiceManager` initialized in lifespan/startup
- [ ] Manager stored on `app.state.ssh_manager`
- [ ] `shutdown()` called in lifespan cleanup / shutdown event
- [ ] Startup does not fail if SSH config is missing (graceful degrade)
- [ ] Lint clean: `flake8 src/api/main.py --config=.flake8`

## Step 3: Agent Core — Profile Loading & Context Building

- [ ] `src/core/agent_core.py` loads user's active SSH profiles from DB in `_execute()`
- [ ] Profile loading happens before `_build_options()` call
- [ ] Only `is_active` profiles are loaded
- [ ] `profile_to_ssh_profile()` converts DB records to `SSHProfile` dataclass
- [ ] `ssh_manager.build_session_context()` called with correct parameters
- [ ] `ssh_context` passed to `create_ag3ntum_tools_mcp_server()` (replaces hardcoded `None`)
- [ ] SSH tool names added to `ag3ntum_tool_names` when `ssh_context` is not `None`:
  - [ ] `mcp__ag3ntum__SSHExec`
  - [ ] `mcp__ag3ntum__SSHRead`
  - [ ] `mcp__ag3ntum__SSHConnect`
- [ ] No SSH tools registered when `ssh_context` is `None`
- [ ] No crash when `ssh_manager` is `None` (e.g., standalone CLI mode)
- [ ] Lint clean: `flake8 src/core/agent_core.py --config=.flake8`

## Step 4: Session Cleanup

- [ ] `_cleanup_session()` in `agent_core.py` calls `ssh_manager.cleanup_session(session_id)`
- [ ] Cleanup is in a try/except — failures logged, never crash the session teardown
- [ ] Cleanup runs in all exit paths (normal completion, error, timeout)
- [ ] Connection pool idle timeout (15 min) still works as fallback
- [ ] Integration test: session end closes SSH connections

## Step 5: SSH System Prompt

- [ ] `prompts/system-prompts/07-ssh.md` created
- [ ] Has `condition: SSH_ENABLED` in frontmatter
- [ ] Contains `${SSH_PROFILES_BLOCK}` variable for profile list
- [ ] Contains tool name variables (`${AG3NTUM_SSH_EXEC_TOOL}`, etc.)
- [ ] Documents readonly mode (L0) — free execution of read commands
- [ ] Documents operations mode (L1+) — dry-run confirmation required
- [ ] Documents single-profile auto-selection rule
- [ ] Documents multi-profile explicit naming rule
- [ ] Documents output truncation at 32KB
- [ ] Documents bounded command usage (`tail -n`, `--no-pager`, `-n` limits)
- [ ] Documents "never expose keys/passphrases" rule
- [ ] Prompt NOT loaded when `SSH_ENABLED` is `False` (no SSH noise)
- [ ] Prompt renders correctly with PromptTemplateEngine
- [ ] Structural test passes (if prompt naming/format is checked)

## Step 6: Prompt Context Injection

- [ ] `SSH_ENABLED` flag set in `prompt_context.flags` based on `ssh_context` existence
- [ ] `SSH_PROFILES_BLOCK` built from `ssh_context.profiles` dict
- [ ] Each profile line includes: name, user@host:port, mode label, description
- [ ] Mode labels map correctly: `readonly` → `L0 readonly`, `operations` → `L1 operations`
- [ ] Tool name variables set: `AG3NTUM_SSH_EXEC_TOOL`, `AG3NTUM_SSH_READ_TOOL`, `AG3NTUM_SSH_CONNECT_TOOL`
- [ ] Variables NOT set when `ssh_context` is `None`
- [ ] Lint clean after prompt context changes

---

## End-to-End Acceptance Criteria

### Single Profile Scenario
- [ ] User creates one SSH profile via UI
- [ ] User opens chat, says "show me the system uptime on my server"
- [ ] Agent auto-selects the only profile, confirms profile name before first command
- [ ] Agent executes `uptime` via SSHExec, returns output in chat
- [ ] No SSH prompt/tools when profile is deleted

### Multi-Profile Scenario
- [ ] User creates two SSH profiles (e.g., `prod-web`, `staging-db`)
- [ ] User says "using prod-web, show me the last 50 lines of nginx access log"
- [ ] Agent uses the named profile, executes `tail -n 50 /var/log/nginx/access.log`
- [ ] Agent does NOT guess profile when user doesn't specify with multiple profiles

### Readonly Mode (L0)
- [ ] Agent freely executes: `ls`, `cat`, `tail`, `grep`, `df`, `ps`, `systemctl status`, `journalctl`
- [ ] Agent uses bounded commands: `tail -n 50`, `journalctl -n 100 --no-pager`
- [ ] Write commands are blocked by command filter

### Operations Mode (L1)
- [ ] Agent shows exact command before executing write operations
- [ ] Agent uses `dry_run=true` when available
- [ ] Agent waits for user confirmation before executing

### Connection Lifecycle
- [ ] Connection established on first SSH command
- [ ] Connection kept alive during session (keepalive)
- [ ] Connection closed when session ends
- [ ] Connection recovered after idle timeout (re-established on next command)

### Security
- [ ] SSH private keys never appear in agent responses
- [ ] Output redaction strips sensitive data before LLM sees results
- [ ] Command audit log records every executed command
- [ ] JWT authentication sufficient (no re-auth for SSH)
- [ ] Output truncated at 32KB

### No-SSH Scenario
- [ ] User with zero SSH profiles gets no SSH tools, no SSH prompt section
- [ ] Agent does not mention SSH capabilities when none are configured
- [ ] No errors in logs when SSH is disabled or unconfigured

---

## Quality Gates

- [ ] All existing SSH tests pass (`test_ssh_profiles.py`, `test_ssh_profiles_unit.py`, `test_ssh_profiles_connection.py`)
- [ ] New integration tests for SSHServiceManager
- [ ] New integration tests for agent_core SSH wiring
- [ ] `./run.sh lint` passes (flake8 + bandit + mypy + eslint + tsc + structural)
- [ ] `./run.sh test --backend` passes
- [ ] `./run.sh test --core` passes
- [ ] No security warnings from bandit on new code
- [ ] Documentation updated:
  - [ ] `docs/source-code-map.md` — add `ssh_service_manager.py`
  - [ ] `../DOCUMENTS/TECHNICAL/current_architecture.md` — mention SSH agent integration
- [ ] Code reviewed for over-engineering (no Phase 2 features in Phase 1)
- [ ] `/simplify` run on all changed files
