# Security Overview (6-Layer)

Read @`../DOCUMENTS/TECHNICAL/layers_of_security_for_filesystem.md` for the full architecture doc.

---

## Layer Summary

| Layer | Component | Files | Scope |
|-------|-----------|-------|-------|
| 0 | WAF | `api/waf_filter.py` | API requests |
| 1 | Docker | `docker-compose.yml` | Container |
| 2 | Bubblewrap + UID | `core/sandbox.py`, `core/uid_security.py` | Bash only |
| 3 | Ag3ntum Tools | `tools/ag3ntum/*`, `core/path_validator.py` | File/cmd ops |
| 4 | Command Filter | `core/command_security.py` | Bash cmds |
| 5 | Middleware | `api/security_middleware.py` | HTTP |
| 6 | Prompts | `prompts/modules/security.md`, `prompts/system-prompts/02-security-constraints.md` | LLM |

---

## Detail

- **Container seccomp**: `seccomp-container.json` applied at container level (replaces previous `seccomp:unconfined`). Per-session seccomp profiles (`seccomp-isolated.json`, `seccomp-direct.json`) layered on top.
- **UID isolation**: Each user → unique UID (50000..60000, ISOLATED mode). OS-enforced via bwrap. `UIDSecurityConfig.__post_init__` validates absolute bounds (50000 min for isolated, 1000 min for direct). Path translation: @`sandbox_path_resolver.md`
- **Shared GID model**: `ag3ntum_api` added to each sandbox user's primary group at creation. Session files use 660/770 (no world access). Cross-user isolation by PathValidator.
- **File ownership**: Write/Edit/MultiEdit tools `chown` files to sandbox user immediately. Session dirs `chown`'d at creation. `ensure_secure_session_files()` re-applies 660/770 post-execution.
- **Read-only source**: `src/` volume mounted read-only (`:ro`) in `docker-compose.yml` to prevent agent modification of application code.
- **WAF hardening**: Body-size-tracking wrapper on `request._receive` prevents Content-Length spoofing bypass of size limits.
- **Auth rate limiting**: Redis-based rate limiting on login (5 failed/account/min, 20 failed/IP/min). Fails open if Redis unavailable.
- **Token revocation**: `token_version` field on User model. Logout increments version, invalidating all outstanding JWTs server-side.
- **Fail-closed**: Security load/validate failure → operation denied. Never catch silently.
- **Secrets scanning**: `src/security/sensitive_data_scanner.py` + `sensitive-data-scanner.yaml` → auto-redacts in File Explorer

---

## API Key Security

- **Storage**: Keys bcrypt-hashed; key prefix (e.g., `ag3_res_abc123`) stored for lookup, full key never persisted
- **Rate limiting**: Per-key rate limits enforced by `APIKeyRateLimiter` (default 100 req/min)
- **IP allowlist**: Optional per-key IP allowlisting with CIDR support (e.g., `10.0.0.0/8`). IPv4-mapped IPv6 addresses (`::ffff:x.x.x.x`) are normalised to IPv4 for matching. IPv6 loopback (`::1`) also checks IPv4 loopback (`127.0.0.1`).
- **Scope control**: 13 granular scopes (`users:create`, `users:read`, `users:update`, `users:suspend`, `users:delete`, `users:password`, `sessions:read`, `usage:read`, `keys:manage`, `config:read`, `config:update`, `skills:manage`, `security:manage`)
- **Audit logging**: All API key usage logged to `api_key_audit_log` — success, auth failures, IP denials, rate limit hits. Admin audit endpoint: `GET /admin/audit` with pagination and filters (reseller_id, action, date range).
- **Spending caps**: 3-tier enforcement (platform → reseller → user) prevents cost overruns at usage recording time. Per-session, daily, and monthly limits.
- **IDOR prevention**: All reseller endpoints scope queries to the authenticated reseller_id. Structural tests enforce this pattern on every endpoint.

---

## Webhook Security

- **HMAC-SHA256 signing**: Each webhook endpoint has a unique secret. Payloads signed with `X-Webhook-Signature` header using `hmac.new(secret, body, sha256)`.
- **Per-endpoint secrets**: Generated at creation time, displayed once to the reseller, never stored in plaintext after initial response.
- **Delivery isolation**: Each reseller can only access their own webhook endpoints (IDOR protected — returns 404 not 403 for cross-reseller access).
- **Scope enforcement**: Creating/updating/deleting webhooks requires `config:update` scope; listing requires `config:read`.
- **Retry with backoff**: Failed deliveries retried with exponential backoff (30s, 2m, 10m, 1h, 6h; max 5 attempts).

---

## Data Retention

- **Configurable purging**: Admin-adjustable retention periods per table. Defaults: `usage_records` 395 days, `events` 30 days, `webhook_delivery_log` 90 days, `api_key_audit_log` 365 days.
- **Background processor**: `RetentionProcessor` runs daily. Manual trigger: `POST /admin/retention/run`.
- **Admin-only access**: Retention config and manual purge restricted to admin role.
