---
name: create_worktree
description: Create a git worktree with isolated Docker stack for a feature branch. Handles port allocation, config inheritance, and optional build. Also supports list and destroy operations.
---

# Create Worktree Instance

Create a new git worktree for parallel development with an isolated Docker stack.
Each worktree gets its own containers, ports, data directory, and configuration.

**Input:** `$ARGUMENTS` — Branch name and optional flags, OR a management command (list, destroy).

## Instructions

### Creating a worktree

1. Parse the arguments. The first argument is the branch name (required). Optional flags:
   - `--name <name>` — Override the instance name (default: branch with `/` replaced by `-`)
   - `--slot <N>` — Force a specific port slot 1-9 (default: auto-allocate next free)
   - `--build` — Also run `./run.sh build` in the new worktree after creation (requires sudo on Linux)
   - `--no-build` — Skip building (this is the default)

2. Run worktree.sh from the project root:
   ```bash
   /Users/greg/EXTRACTUM/Ag3ntum/Project/worktree.sh create <branch> [--name <name>] [--slot <N>]
   ```

3. Report the result to the user:
   - Worktree directory path (at `../Project_wt_<name>/`)
   - Assigned ports (API, Web, Redis)
   - COMPOSE_PROJECT_NAME
   - Next steps to build and access the instance

4. If `--build` was specified, run the build in the new worktree directory.
   This requires sudo on Linux — use the `interactive-bash` MCP tool if available:
   ```bash
   cd <worktree_dir> && ./run.sh build
   ```

### Listing worktrees

If the user says "list" or wants to see all instances:
```bash
/Users/greg/EXTRACTUM/Ag3ntum/Project/worktree.sh list
```

### Destroying a worktree

If the user says "destroy <name>" or wants to remove an instance:
```bash
/Users/greg/EXTRACTUM/Ag3ntum/Project/worktree.sh destroy <name>
```

### Getting status

If the user wants detailed status of an instance:
```bash
/Users/greg/EXTRACTUM/Ag3ntum/Project/worktree.sh status <name>
```

## Port Allocation Reference

Each slot gets a block of 10 ports:

| Slot | API Port | Web Port | Redis Port |
|------|----------|----------|------------|
| 0 (main) | 40080 | 50080 | 46379 |
| 1 | 40090 | 50090 | 46389 |
| 2 | 40100 | 50100 | 46399 |
| 3 | 40110 | 50110 | 46409 |
| 4 | 40120 | 50120 | 46419 |

## Examples

```
/create_worktree feature/auth
/create_worktree feature/auth --build
/create_worktree feature/auth --name auth --slot 2
/create_worktree list
/create_worktree destroy auth
/create_worktree status auth
```
