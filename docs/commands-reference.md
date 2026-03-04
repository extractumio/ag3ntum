# Ag3ntum Commands Reference

## Worktree Commands (Multi-Instance Support)

```bash
./worktree.sh create <branch>                     # Create worktree with isolated Docker stack
./worktree.sh create <branch> --name N --slot S    # Explicit name and port slot
./worktree.sh list                                 # List all instances with ports and status
./worktree.sh status <name>                        # Detailed status of an instance
./worktree.sh destroy <name>                       # Stop Docker stack and remove worktree
```

**Claude Code command**: `/create_worktree <branch> [--build]` — agentic worktree creation

## When to Rebuild

| Change | Command |
|--------|---------|
| Code / config YAML | `./run.sh restart` |
| Dockerfile / requirements.txt | `./run.sh build --no-cache` |
| External mounts (add/remove/path) | `./run.sh build` |
| Switch prod ↔ dev mode | `./run.sh build` or `./run.sh build --dev` |
| Frontend code (prod mode) | `./run.sh build` (dev mode: automatic via HMR) |
| Full reset | `./run.sh rebuild` |

## Deployment Modes

Both modes use two ports: **Web UI** http://localhost:50080 | **API** http://localhost:40080

| Mode | Command | How it works |
|------|---------|--------------|
| **Production** (default) | `./run.sh build` | Web container serves pre-built static bundle (fast startup, no npm install) |
| **Development** | `./run.sh build --dev` | Web container runs Vite dev server with HMR (hot-reload) |

Mode is persisted in `.env` as `AG3NTUM_MODE`. `install.sh` defaults to prod/release; use `install.sh --dev` for development.

## Upgrade

```bash
./upgrade.sh                           # Full upgrade: backup → pull → migrate → build → validate
./upgrade.sh --dry-run                 # Preview upgrade plan without making changes
./upgrade.sh --force                   # Skip confirmation prompts
./upgrade.sh --skip-backup             # Skip backup step (for CI/CD)
./upgrade.sh --rollback                # Restore from most recent backup
./upgrade.sh --check                   # Health diagnostics only
```

The upgrade script handles:
- **Pre-flight checks**: git clean, Docker running, disk space, active sessions
- **Backup**: `data/ config/ .env .ag3ntum-version` → `backups/` (keeps 3)
- **Dependency detection**: auto-uses `--no-cache` if `requirements.txt`, `package.json`, or `Dockerfile` changed
- **Config migration**: runs `scripts/migrate_config.py` for config schema changes
- **Database migration**: Alembic runs automatically in the entrypoint on container start
- **Post-validation**: health check, DB verify, version match
- **Rollback**: `--rollback` restores from latest backup and rebuilds

**Do not use** `git pull && ./run.sh build` directly — it skips backup, config migration, and dependency change detection.

## User Management

```bash
./run.sh create-user                   # Interactive user creation
./run.sh delete-user                   # Interactive user deletion
./run.sh cleanup-test-users            # Remove test users (UIDs 59990+)
```
