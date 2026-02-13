# Diagnostics & Troubleshooting

---

## Logs

| Log | Content |
|-----|---------|
| `logs/backend.log` | API server (10MB rotation, 5 backups) |
| `logs/agent_cli.log` | CLI execution |
| `logs/latest-test-results.log` | Last test run (overwritten) |

```bash
docker logs project-ag3ntum-api-1 --tail 100 -f     # Container stdout
./run.sh shell && tail -f /logs/backend.log          # Inside container
grep -i "denied\|blocked" logs/backend.log           # Security denials
grep "ERROR\|Exception" logs/backend.log             # Errors
```

**Loggers**: `src.api` | `src.services` | `src.core` | `src.db` | `ag3ntum` | `tools.ag3ntum` | `uvicorn` | `fastapi`

---

## Database

`sqlite3 data/ag3ntum.db` — tables: `users`, `sessions`, `events`, `tokens`

```sql
-- List recent sessions
SELECT id, status, task, total_cost_usd FROM sessions ORDER BY created_at DESC LIMIT 10;
-- Check user UIDs (sandbox debug)
SELECT username, linux_uid FROM users WHERE linux_uid BETWEEN 50000 AND 60000;
-- Count events for session
SELECT COUNT(*) FROM events WHERE session_id = 'SESSION_ID';
-- Find terminal event
SELECT event_type FROM events WHERE session_id = 'SESSION_ID'
  AND event_type IN ('agent_complete', 'error', 'cancelled');
```

---

## Debug Agent Execution

```bash
./venv/bin/python scripts/ag3ntum_debug.py -r "task" --user "email" --password "pass"
# -v  verbose (all events)
# -s  security only (blocked ops)
# -d  dump session files
# -m/--model  override model (e.g., "openrouter:openai/gpt-5.2")
```
Read @`how-to-debug-agent-with-ag3ntum_debug.md`. Note: auth uses email, filesystem uses username.

---

## Troubleshooting Flowcharts

**Session stuck in "running"**:
1. Check process: `ps aux | grep session_id` inside container
2. Check DB: `SELECT status, updated_at FROM sessions WHERE id = '...';`
3. Fix: `./run.sh restart` — cleans stale sessions on startup

**Events not appearing in UI**:
1. Redis alive? `redis-cli ping` (inside container)
2. Events persisted? `SELECT COUNT(*) FROM events WHERE session_id = '...';`
3. Browser console → SSE connection errors?
4. JWT token valid? Check expiry in browser DevTools.

**Agent failing silently**:
1. Check SDK log: `tail -50 users/USER/sessions/ID/agent.jsonl | grep -i error`
2. Check backend: `grep -A5 "Exception\|Traceback" logs/backend.log | tail -30`

**Container won't start**:
1. Port conflict: `lsof -i :40080` / `lsof -i :50080`
2. Stale containers: `./run.sh cleanup && ./run.sh build`
3. Permission issue (Linux): `./run.sh build` re-runs chown

**Tests failing unexpectedly**:
1. Check `logs/latest-test-results.log` for full output
2. Stale container? `./run.sh rebuild && ./run.sh test`
3. Redis down? Tests need Redis: `docker ps | grep redis`
4. Wrong platform binaries (UI tests)? `run.sh` auto-detects and reinstalls node_modules

**SSE streaming broken**:
1. Frontend falls back: SSE → backoff → polling (3+ fails) → SSE retry (60s)
2. Check `ConnectionManager` state in React DevTools
3. Check `/sessions/{id}/events` endpoint in Network tab
4. Fallback endpoint: `/sessions/{id}/events/history` (polling)
