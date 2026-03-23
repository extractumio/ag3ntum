# Using SSH Tools

Ag3ntum SSH tools let the agent manage remote servers through secure, filtered SSH connections. The agent can run commands, read files, and manage connections — all governed by a 4-tier privilege model enforced at the Python level, not by LLM prompts.

---

## Quick Start (5 minutes)

### 1. Copy config files

```bash
cp config/security/ssh-security.yaml.example config/security/ssh-security.yaml
cp config/security/ssh-privilege-levels.yaml.example config/security/ssh-privilege-levels.yaml
```

### 2. Enable SSH

Edit `config/security/ssh-security.yaml`:

```yaml
ssh:
  enabled: true
  default_mode: readonly
```

### 3. Store your SSH key in the vault

SSH keys are encrypted in the database, not stored as files. Run this inside the API container:

```bash
./run.sh shell
python3 /src/scripts/vault_store_key.py \
  --user <username> \
  --name my-server-key \
  --key-file /tmp/my_ed25519_key \
  --key-type ed25519
```

If the helper script is not yet available, use the Python one-liner approach described in [Storing Keys in the Vault](#storing-keys-in-the-vault) below.

### 4. Create a connection profile

Create `users/<username>/ag3ntum/ssh-profiles.yaml`:

```yaml
profiles:
  my-server:
    host: 203.0.113.10
    port: 22
    username: deploy
    auth_method: key
    key_ref: my-server-key       # matches the --name from step 3
    mode: readonly
    privilege_level: 0            # P0: read-only monitoring
    description: "Production web server"
```

### 5. Restart

```bash
./run.sh restart
```

The agent now has three SSH tools: `SSHConnect`, `SSHExec`, and `SSHRead`. At P0, it can run `uptime`, `df -h`, read logs, check service status — nothing destructive.

---

## Storing Keys in the Vault

This is the most important step to understand. SSH keys are **not** placed in a file path. They are encrypted with a per-user derived key (HKDF-SHA256 from the master encryption key + the user's JWT secret) and stored in the `vault_secrets` database table. The agent never sees the raw key material — it is decrypted in memory only during SSH connection establishment.

### Why not just a file?

- Files on disk can be read by the agent (if it finds the path)
- Files persist in plaintext in container layers and backups
- Vault entries are encrypted per-user: even a database dump is useless without the user's JWT secret
- Every vault access is logged in `vault_audit_logs`

### Method 1: Python script (recommended)

From inside the API container (`./run.sh shell`):

```python
import asyncio
from src.db.database import async_session_factory
from src.services.vault_encryption import VaultEncryption
from src.services.vault_service import VaultService
from src.services.encryption_service import encryption_service

async def store_key():
    # Read the private key file
    with open("/tmp/my_ed25519_key", "r") as f:
        key_pem = f.read()

    # Initialize vault with the instance's master key
    vault_enc = VaultEncryption(master_key=encryption_service.key)
    vault = VaultService(vault_encryption=vault_enc)

    async with async_session_factory() as db:
        secret = await vault.store_secret(
            db,
            user_id="<user-uuid>",          # from users table
            secret_type="ssh_private_key",
            name="my-server-key",            # this is the key_ref in profiles
            plaintext_value=key_pem,
            ssh_key_type="ed25519",          # metadata only
            description="Production server deploy key",
        )
        print(f"Stored secret id={secret.id}, name={secret.name}")

asyncio.run(store_key())
```

**Finding the user UUID**: Query the database or check the API:

```bash
# Inside container
sqlite3 /data/ag3ntum.db "SELECT id, username FROM users;"
```

### Method 2: Store a password (if key auth is not available)

Password auth is disabled by default. To enable it, set `password_auth_allowed: true` in `ssh-security.yaml`, then:

```python
secret = await vault.store_secret(
    db,
    user_id="<user-uuid>",
    secret_type="password",
    name="my-server-password",
    plaintext_value="the-actual-password",
    description="Server login password",
)
# Use password_secret_id in profile:
print(f"Use password_secret_id: {secret.id}")
```

Then in the profile:

```yaml
profiles:
  my-server:
    host: 203.0.113.10
    username: deploy
    auth_method: password
    password_secret_id: 7          # the id printed above
    mode: readonly
    privilege_level: 0
```

### Method 3: Store a certificate

For certificate-based auth (e.g., HashiCorp Vault-signed certificates):

```python
# Store the private key
key_secret = await vault.store_secret(
    db, user_id, "ssh_private_key", "cert-key", key_pem,
    ssh_key_type="ed25519",
)

# Store the certificate
cert_secret = await vault.store_secret(
    db, user_id, "ssh_certificate", "cert-cert", cert_pem,
    description="Signed by internal CA, expires 2026-03-01",
    expires_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
)
```

Profile:

```yaml
profiles:
  my-server:
    auth_method: certificate
    key_ref: cert-key                # vault name of the private key
    certificate_ref: 12              # vault secret ID of the certificate
```

### Important: password changes invalidate vault entries

Vault encryption is derived from `master_key + user.jwt_secret`. When a user changes their password, their `jwt_secret` rotates, and **all their vault entries become inaccessible**. After a password change, re-store all SSH keys for that user.

---

## Connection Profiles

Profiles define how the agent connects to each server. Each user has their own profiles file:

```
users/<username>/ag3ntum/ssh-profiles.yaml
```

### Full profile reference

```yaml
profiles:
  prod-web:
    # Required
    host: 203.0.113.10
    username: deploy

    # Connection
    port: 22                        # default: 22

    # Authentication (pick one method)
    auth_method: key                # key | certificate | password
    key_ref: prod-web-key           # vault secret name (for key/certificate)
    certificate_ref: null           # vault secret ID (for certificate only)
    password_secret_id: null        # vault secret ID (for password only)

    # Access control
    mode: readonly                  # readonly | operations | filtered_shell
    privilege_level: 0              # 0-3 (see Privilege Levels below)

    # Operations mode: explicit list of allowed commands
    allowed_operations: []          # only used when mode=operations

    # Metadata
    description: "Production web server"
    file_handling: {}               # reserved for future context isolation config
```

### Access modes

| Mode | Description | Use case |
|------|-------------|----------|
| `readonly` | Agent can run allowed read-only commands and read files via SFTP | Monitoring, log review, diagnostics |
| `operations` | Agent can only run commands from `allowed_operations` list | Targeted automation (restart service, deploy) |
| `filtered_shell` | Agent can run any command not in the blocklist | Broad administration (P2/P3 only) |

### Multiple profiles per user

```yaml
profiles:
  prod-web:
    host: 203.0.113.10
    username: deploy
    auth_method: key
    key_ref: prod-key
    mode: readonly
    privilege_level: 0

  staging-web:
    host: 10.0.1.20
    username: admin
    auth_method: key
    key_ref: staging-key
    mode: filtered_shell
    privilege_level: 2

  db-primary:
    host: 10.0.1.30
    username: dba
    auth_method: key
    key_ref: db-key
    mode: operations
    privilege_level: 1
    allowed_operations:
      - "sudo systemctl restart postgresql"
      - "sudo systemctl reload postgresql"
```

---

## Privilege Levels (P0 -- P3)

The command filter enforces a 4-tier privilege model. Every command the agent tries to run passes through this filter before reaching the SSH connection. The filter operates at the Python regex level — the LLM has zero influence on security decisions.

### P0: Observer (read-only allowlist)

**Mode**: Allowlist — commands must match one of ~21 pre-approved patterns.

The agent can:
- Read log files: `cat /var/log/syslog`, `tail -f /var/log/nginx/access.log`
- Check services: `systemctl status nginx`, `journalctl -u postgresql --no-pager`
- System info: `uptime`, `df -hT`, `free -h`, `uname -a`, `hostname`, `whoami`, `id`
- Process/network: `ps aux`, `netstat -tlnp`, `ss -tlnp`
- DNS/connectivity: `dig example.com`, `ping -c 4 host`
- Version checks: `php -v`, `python3 --version`, `node --version`
- Container status: `docker ps`, `docker stats --no-stream`
- Config validation: `nginx -t`, `apache2ctl -t`
- SSL check: `openssl s_client -connect host:443`
- OS info: `cat /etc/os-release`

The agent **cannot** (at P0): write files, restart services, use sudo, run arbitrary commands.

### P1: Site Manager

Complete WordPress site management. Inherits P0 commands, plus:

- File writes in `/var/www/` (WordPress root)
- `wp-cli` full access (plugins, themes, settings, database)
- `mysqldump` for WordPress database backups
- `sudo systemctl restart|reload|start|stop` for web services (nginx, apache, php-fpm)
- `sudo service` for web services
- `composer` and `git` for dependency/version management
- `certbot` for SSL certificate management (create, renew, revoke)
- Shell redirects (`>`, `tee`) allowed for WordPress paths

**Human approval**: Per-session baseline. Destructive operations require per-command approval: `rm -rf` on directories, `DROP/TRUNCATE`, `wp user delete`, `mysqldump` (first invocation), database import, `wp eval/eval-file/shell`.

### P2: Server Admin

Full server administration with general restrictions. Inherits P1 commands, plus:

- Package management: `apt`, `yum`, `dpkg`, `rpm`
- User and group management: `useradd`, `usermod`, `passwd`
- Firewall management: `ufw`, `firewall-cmd`, `iptables`
- Cgroup and systemd: `systemctl` (full control including enable/disable for non-critical services)
- Docker management: `docker`, `docker-compose`
- System configuration files: `/etc/` (except shadow, passwd, sudoers, sshd_config, pam.d)
- Crontab editing for system maintenance tasks
- File writes to config directories
- Shell redirects allowed system-wide

**Blocked at P2**:
- `/etc/shadow`, `/etc/passwd`, `/etc/sudoers` (user account manipulation)
- `/etc/ssh/sshd_config`, `/etc/pam.d/` (auth bypass)
- `sudo rm -rf /` (filesystem destruction)
- Fork bombs and resource exhaustion

**Human approval**: Per destructive command.

### P3: Full Access

Emergency unrestricted access, time-boxed to 60 minutes.

**Minimal blocklist** — only `sudo rm -rf /` and fork bombs are blocked.

**Additional restrictions**:
- Maximum session duration: 60 minutes (`max_session_duration_seconds: 3600`)
- Requires re-authentication
- Human approval: per command

### Hard-blocked (all levels, including P3)

These patterns cannot be overridden by any privilege level:

**Persistence prevention**:
- Writing to `authorized_keys`
- `LD_PRELOAD` / `ld.so.preload` library injection

**Lateral movement prevention**:
- Nested `ssh` or `scp` commands
- `curl ... | bash` or `wget ... -O - |` (download-and-execute)
- `nc -lp`, `ncat -lp`, `socat` (reverse shells / listeners)
- Cloud metadata endpoint (`169.254.169.254`)
- `base64 -d | bash` (encoded execution)

**Data exfiltration** (requires human approval, not blocked outright):
- Reading `.env`, `.key`, `.pem`, `.p12`, `.pfx`, `.jks` files

### Level-gated capabilities

These were previously always-blocked but are now unlocked at higher profiles:

| Capability | Blocked at | Allowed at |
|---|---|---|
| `crontab -e -u www-data` | P0 | P1+ |
| `crontab -e` (own user) | P0-P1 | P2+ |
| `systemctl enable/disable` | P0-P1 | P2+ |
| `systemctl mask` | P0-P2 | P3 |
| `sed -i` (web paths) | P0 | P1+ |
| `sed -i` (system paths) | P0-P1 | P2+ |
| `tee`, `>`, `>>` (web paths) | P0 | P1+ |
| `$VAR`, `${}` | P0 | P1+ |
| `$()`, backticks | P0-P1 | P2+ |
| `bash -c`, `sh -c` | P0-P1 | P2+ |
| `eval`, `exec` | P0-P2 | P3 |
| `mysqldump` | Approval | P1+ (first invocation needs approval) |

---

## Security Configuration Reference

`config/security/ssh-security.yaml` — full reference:

```yaml
ssh:
  # Master switch — must be explicitly enabled
  enabled: false

  # Default access mode for new connections
  default_mode: readonly            # readonly | operations | filtered_shell

  # Connection and execution limits
  limits:
    max_connections_per_user: 3     # concurrent SSH connections per user
    max_concurrent_commands: 5      # parallel command executions
    session_timeout_seconds: 1800   # 30 min — connection lifetime
    command_timeout_seconds: 300    # 5 min — per-command timeout
    max_output_bytes: 1048576       # 1MB — stdout/stderr cap per command
    max_file_read_bytes: 5242880    # 5MB — max file size for SSHRead
    max_file_write_bytes: 1048576   # 1MB — max file size for writes
    rate_limit_commands_per_minute: 30

  # Host access control
  hosts:
    mode: allowlist                 # allowlist | blocklist
    always_blocked:                 # never connectable, regardless of mode
      - "127.0.0.1"
      - "localhost"
      - "::1"
      - "169.254.0.0/16"           # AWS/GCP metadata endpoint
    private_network_exceptions: []  # specific private IPs that ARE allowed
                                    # e.g., ["192.168.1.10", "10.0.1.5"]

  # Credential policy
  credentials:
    key_storage_encryption: fernet
    allowed_key_types:
      - ed25519                     # preferred
      - rsa-4096
    prohibited_key_types:
      - dsa                         # broken
      - rsa-1024                    # too short
      - rsa-2048                    # below modern standards
    certificate_support: true
    certificate_ca_url: null        # HashiCorp Vault URL for signed certs
    certificate_ttl_seconds: 3600   # 1 hour certificate lifetime
    password_auth_allowed: false    # disabled by default

  # Sensitive file handling
  context_isolation:
    enabled: true
    auto_detect_sensitive: true     # detect .env, keys, configs
    always_redact_secrets: true     # redact API keys in output
    max_context_file_size_bytes: 102400  # 100KB

  # Audit logging (to SQLite)
  audit:
    enabled: true
    log_commands: true
    log_file_access: true
    log_connection_events: true
    sensitive_command_alert: true
    retention_days: 90

  # Behavioral anomaly detection
  behavior_monitor:
    enabled: true
    anomaly_detection: true
    circuit_breaker_threshold: 10   # consecutive failures to trip breaker
    command_pattern_window_seconds: 60
```

---

## How the Agent Uses SSH Tools

The agent has three MCP tools. It uses them the same way it uses Read, Write, or Bash — they appear as available tools in the LLM context.

### SSHConnect — Connection lifecycle

```
SSHConnect(action="list")
SSHConnect(profile_name="prod-web", action="connect")
SSHConnect(profile_name="prod-web", action="status")
SSHConnect(profile_name="prod-web", action="disconnect")
```

Connections are persistent within a session. `SSHExec` and `SSHRead` connect automatically if not already connected — explicit `connect` is optional.

### SSHExec — Run a command

```
SSHExec(profile_name="prod-web", command="uptime")
SSHExec(profile_name="prod-web", command="df -hT")
SSHExec(profile_name="prod-web", command="systemctl status nginx")
```

Returns exit code, stdout, stderr, and duration. Output is truncated at `max_output_bytes`.

### SSHRead — Read a remote file

```
SSHRead(profile_name="prod-web", path="/etc/nginx/nginx.conf")
SSHRead(profile_name="prod-web", path="/var/log/nginx/error.log")
```

Returns file contents with line numbers (same format as the local Read tool). Files exceeding `max_file_read_bytes` are refused before transfer.

---

## Connection Lifecycle

### Persistent connections

Connections survive across agent turns within a session. The agent does not need to reconnect for every command — the connection pool handles this transparently.

### Keepalive

SSH-level keepalive packets are sent every 30 seconds. If 3 consecutive keepalives fail (90 seconds with no response), the connection is marked dead.

### Idle timeout

A watchdog timer closes connections after 15 minutes (configurable via `session_timeout_seconds`) of inactivity. Any SSH operation resets the timer.

### Transparent reconnection

If a connection drops (network issue, server restart), the next `SSHExec` or `SSHRead` call automatically reconnects using the same vault credentials. The agent does not see the reconnection — it is handled by the connection pool.

### Session end

When an agent session completes, all SSH connections for that session are closed and logged.

### Health checker

A background task runs every 60 seconds, checking all connections for zombies (connections that report as open but have a dead underlying transport). Zombies are cleaned up automatically.

---

## Audit Trail

Every SSH operation is logged to the `ssh_audit_events` table in SQLite. The agent cannot read or modify audit records.

### What is logged

| Event | Logged data |
|-------|------------|
| Command execution | session, user, profile, host, command, exit code, output size, duration, privilege level |
| Blocked command | same as above + block reason, matching rule |
| File read/write | session, user, profile, host, path, operation type |
| Connection events | connect, disconnect, reconnect, failed |
| Anomalies | type, details, associated session |

### Querying audit data

Audit data is accessible through the `SSHAuditService` (not exposed to the agent):

```python
# In admin code or API route
from src.services.ssh_audit_service import ssh_audit_service

# Get all events for a session
events = await ssh_audit_service.query_by_session(db, session_id)

# Get blocked attempts for a user
blocked = await ssh_audit_service.query_blocked(db, user_id, hours=24)

# Get aggregate stats
stats = await ssh_audit_service.get_stats(db, user_id)
# Returns: total_commands, total_blocked, unique_hosts, total_file_accesses, total_anomalies
```

---

## Troubleshooting

### "SSH is disabled"

The agent returns `SSH is disabled. Enable it in the security configuration.`

**Fix**: Set `enabled: true` in `config/security/ssh-security.yaml` and restart (`./run.sh restart`).

### "SSH profile 'xxx' not found"

The agent cannot find the named profile.

**Check**:
1. Profile file exists at `users/<username>/ag3ntum/ssh-profiles.yaml`
2. Profile name in YAML matches what the agent is using
3. The YAML is valid (no syntax errors)
4. Restart after adding profiles (`./run.sh restart`)

### "Command blocked by security filter"

The command did not pass the privilege level filter.

**Check**:
1. What privilege level is set on the profile? (`privilege_level: 0` is the most restrictive)
2. At P0, only ~21 specific commands are allowed — check the full list above
3. `hard_blocked` patterns cannot be overridden at any level
4. View block reason in the error message — it tells you which rule matched

### "SSH credential error"

The vault cannot retrieve or decrypt the SSH key.

**Check**:
1. The `key_ref` in the profile matches the `name` used when storing the key
2. The key was stored for the correct `user_id`
3. The user has not changed their password since the key was stored (password change invalidates vault entries)
4. The key is still active (`is_active=True` in the database)

### "SSH connection failed"

The SSH connection itself failed.

**Check**:
1. Is the remote host reachable from the Ag3ntum container? (`ping` from inside container)
2. Is the SSH port open? (`nc -zv <host> <port>`)
3. Is the SSH key authorized on the remote server? (test manually with `ssh -i`)
4. Check `hard_blocked` hosts — localhost, `127.0.0.1`, `::1`, and `169.254.0.0/16` are always blocked
5. Private network IPs (10.x, 172.16-31.x, 192.168.x) require listing in `private_network_exceptions`

### "SSH connection limit reached"

Too many concurrent connections.

**Fix**: Close unused connections with `SSHConnect(action="disconnect", profile_name="xxx")` or increase `max_connections_per_user` in the security config.

### "File too large to read"

The remote file exceeds `max_file_read_bytes` (default 5MB).

**Workaround**: Use `SSHExec` with `head`, `tail`, or `grep` to read portions of the file.

### "Command requires human approval"

Commands matching data exfiltration patterns (`mysqldump`, `pg_dump`, `tar -czf`) require explicit user approval before execution.

**Action**: The user must approve the operation through the Ag3ntum UI before the agent can proceed.

### Vault entries inaccessible after password change

When a user changes their password, their `jwt_secret` rotates, invalidating all vault-encrypted secrets.

**Fix**: Re-store all SSH keys for the affected user using the vault storage procedure described above.

### Connection drops after 15 minutes idle

This is by design — the idle timeout closes unused connections. The next SSH operation will reconnect automatically. To change the timeout, adjust `session_timeout_seconds` in the security config.

### Verifying stored vault secrets

To check what secrets exist for a user (without seeing plaintext):

```python
secrets = await vault.list_secrets(db, user_id, secret_type="ssh_private_key")
for s in secrets:
    print(f"  id={s['id']} name={s['name']} active={s['is_active']} type={s['ssh_key_type']}")
```

### Checking audit logs for blocked commands

```bash
# Inside container
sqlite3 /data/ag3ntum.db \
  "SELECT timestamp, command, block_reason, block_rule FROM ssh_audit_events WHERE blocked=1 ORDER BY timestamp DESC LIMIT 20;"
```

---

## Migration from L0-L4 to P0-P3

If you created SSH profiles under the previous 5-tier system (L0-L4), apply this mapping:

| Old Level | New Level | Action |
|---|---|---|
| L0 (Monitoring) → | P0 (Observer) | No change needed (privilege_level=0) |
| L1 (Service Management) → | P1 (Site Manager) | No change needed (privilege_level=1) |
| L2 (Configuration) → | P2 (Server Admin) | Change to privilege_level=2. P2 is now broader than old L2 |
| L3 (Administration) → | P2 (Server Admin) | Change privilege_level from 3 to 2 |
| L4 (Emergency) → | P3 (Full Access) | Change privilege_level from 4 to 3 |

Profiles with `privilege_level=4` will be rejected by the API validator (max is now 3).
Update via the UI or API before restarting after upgrade.
