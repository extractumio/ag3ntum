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

## User Management

```bash
./run.sh create-user                   # Interactive user creation
./run.sh delete-user                   # Interactive user deletion
./run.sh cleanup-test-users            # Remove test users (UIDs 59990+)
```
