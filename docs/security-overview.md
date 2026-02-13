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
