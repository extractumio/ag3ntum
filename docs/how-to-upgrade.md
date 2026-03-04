# How to Upgrade Ag3ntum

## Standard Upgrade

```bash
./upgrade.sh
```

This runs the full upgrade pipeline:

1. **Pre-flight checks** — git repo, Docker running, disk space, active sessions
2. **Backup** — tars `data/`, `config/`, `.env`, `.ag3ntum-version` to `backups/`
3. **Pull code** — `git fetch` + `git pull --rebase origin main`
4. **Dependency detection** — if `requirements.txt`, `package.json`, or `Dockerfile` changed, uses `--no-cache` build
5. **Stop services** — `docker compose down`
6. **Config migration** — applies config schema changes between versions
7. **Build** — `./run.sh build` (or `--no-cache` if dependencies changed)
8. **Validation** — health endpoint, DB check, version match

Duration: 3-10 minutes depending on whether dependencies changed.

## Options

| Flag | Effect |
|------|--------|
| `--dry-run` | Preview what would happen without making changes |
| `--force` | Skip confirmation prompts and session warnings |
| `--skip-backup` | Skip backup step (for CI/CD) |
| `--rollback` | Restore from most recent backup |
| `--check` | Health diagnostics only |

## Preview Before Upgrading

```bash
./upgrade.sh --dry-run
```

Shows the target version, dependency changes, and config migration plan without modifying anything.

## Health Check

```bash
./upgrade.sh --check
```

Reports: installed vs codebase version, Docker status, database health, API health, disk space, and available backups.

## Rollback

```bash
./upgrade.sh --rollback
```

Restores `data/`, `config/`, `.env`, and `.ag3ntum-version` from the most recent backup in `backups/`, then rebuilds. Use this if an upgrade caused issues.

## Backups

- Location: `backups/upgrade-{version}-{timestamp}.tar.gz`
- Contains: `data/`, `config/`, `.env`, `.ag3ntum-version`
- Retention: 3 most recent (older auto-pruned)
- Permissions: 600 (owner-only)

## What Happens to the Database

Database schema migrations use Alembic and run automatically during container startup (in the entrypoint, before the API starts). You do not need to run them manually.

Three scenarios are handled:

| Database State | Action |
|---------------|--------|
| Fresh (no tables) | `create_all()` + stamp at head revision |
| Existing without Alembic tracking | Stamp at revision 001, then upgrade to head |
| Existing with Alembic tracking | Run pending migrations to head |

## Config Migration

Config files may gain new keys between versions. The upgrade script migrates configs automatically:

```bash
# Manual dry-run (if needed)
python3 scripts/migrate_config.py --from 0.2.0 --to 0.3.0 --dry-run
```

Before modifying any config file, the migration creates a `.pre-{version}.bak` backup in `config/`.

## Manual Upgrade (Not Recommended)

If you must upgrade without the script:

```bash
# 1. Backup manually
tar czf backup.tar.gz data/ config/ .env .ag3ntum-version

# 2. Pull
git pull --rebase origin main

# 3. Check if dependencies changed (requires --no-cache if yes)
git diff HEAD~1 -- requirements.txt package.json Dockerfile

# 4. Build
./run.sh build          # or: ./run.sh build --no-cache

# 5. Update version marker
cp VERSION .ag3ntum-version
```

This skips config migration, session safety checks, and post-upgrade validation.

## Troubleshooting

**Version mismatch warning on `./run.sh build`:**
The codebase version differs from `.ag3ntum-version`. Use `./upgrade.sh` for a proper upgrade, or wait 5 seconds to continue with a direct build.

**Upgrade fails during build:**
Run `./upgrade.sh --check` to diagnose. Retry with `./run.sh build --no-cache`.

**Health check fails after upgrade:**
Check container logs: `docker compose logs ag3ntum-api`. If the database is corrupted, use `./upgrade.sh --rollback`.

**Config migration error:**
Check `config/*.pre-{version}.bak` files — your original configs are preserved. Restore manually if needed.
