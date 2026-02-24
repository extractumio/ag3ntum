# Self-Review Skill

Run this skill before committing to catch issues early. It runs linting, structural tests, and verifies no debug artifacts remain.

## Steps

1. **Run linters**: Execute `./run.sh lint` and review any failures
2. **Check for debug artifacts**: Search modified files for:
   - `console.log` (frontend)
   - `print(` without `# noqa: print-ok` (backend)
   - `debugger` statements
   - `TODO` or `FIXME` comments added in this session
   - Commented-out code blocks
3. **Verify imports**: Ensure no unused imports were added
4. **Run structural tests**: `python3 -m pytest tests/structural/ -v`
5. **Review modified files**: Re-read each modified file and verify:
   - Changes match the intent of the task
   - No unintended side effects
   - Error handling is appropriate
6. **Report**: Summarize findings. If all checks pass, indicate ready to commit.

## When to Use

- Before every commit
- After implementing a feature or fix
- After refactoring
