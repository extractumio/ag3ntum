# Testing Guide

All tests run **inside Docker** via `docker-compose.test.yml` (root → drops to ag3ntum_api via `setpriv --init-groups`, `AG3NTUM_TEST_MODE=true`).

**Test entrypoint** (`entrypoint-test.sh`): Installs test sudoers, syncs Linux users from DB, creates fully-equipped test users (`ag3ntum_tester_a` UID 59990, `ag3ntum_tester_b` UID 59991) with DB entries, venvs, persistent storage, and shared GID memberships, then drops privileges. Test users are at the high end of the isolated range to avoid conflicts with real users. Credentials: email `ag3ntum_tester_a@test.local`, password `TestPassword123!`.

---

## Writing Backend Tests

```python
# tests/backend/test_<module>.py
class TestFeature:
    @pytest.mark.unit
    async def test_behavior(self, test_app, auth_headers):
        response = await test_app.get("/endpoint", headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.e2e
    async def test_full_flow(self, test_app): ...
```

**Backend fixtures** (`conftest.py`): `db_engine`/`db_session` (in-memory SQLite) | `test_app` (FastAPI client) | `auth_headers` (JWT) | `mock_agent_runner` | `temp_session_dir` | `test_user_manager`

**Redis fixtures** (`redis/conftest.py`): `redis_connection` | `event_hub` | `tracer_factory`

---

## Writing E2E / Functional Tests (Real Users)

Tests that need real Linux users (sandbox execution, filesystem permissions, mount access, user isolation) **must reuse pre-built test users**, not create them dynamically.

**Why**: The API process gets its supplementary groups at startup via `setpriv --init-groups`. Dynamically-created users add `ag3ntum_api` to the new user's group, but the already-running API process doesn't pick up the change. This causes `Permission denied` on session workspace directories. Restarting the container mid-test is not viable.

**Pre-built test users** (created by `entrypoint-test.sh`):

| Field | tester_a | tester_b |
|-------|----------|----------|
| Username | `ag3ntum_tester_a` | `ag3ntum_tester_b` |
| UID/GID | 59990 | 59991 |
| Email | `ag3ntum_tester_a@test.local` | `ag3ntum_tester_b@test.local` |
| Password | `TestPassword123!` | `TestPassword123!` |
| Home | `/users/ag3ntum_tester_a` | `/users/ag3ntum_tester_b` |

Both have: Linux accounts, DB entries, Python venvs, persistent storage, shared GID memberships, `.claude/skills/` dirs.

**Pattern for E2E tests**:
```python
from types import SimpleNamespace

# Constants (reuse across test files)
PREBUILT_USER_A_USERNAME = "ag3ntum_tester_a"
PREBUILT_USER_A_UID = 59990

def _prebuilt_user(username: str, uid: int) -> SimpleNamespace:
    return SimpleNamespace(username=username, linux_uid=uid)

# Fixture — no DB session needed
@pytest.fixture
def test_user(self) -> SimpleNamespace:
    return _prebuilt_user(PREBUILT_USER_A_USERNAME, PREBUILT_USER_A_UID)

# For API auth, login with known credentials
response = await client.post("/auth/token", json={
    "email": "ag3ntum_tester_a@test.local",
    "password": "TestPassword123!",
})
```

**Rules**:
- Prefix test artifacts with `_test_` or `_e2e_` for easy cleanup
- Always clean up test files in fixture teardown (pre-built users persist across runs)
- Use `try/finally` for cleanup in test bodies that create files
- Only `TestRealUserCreation` in `test_real_user_integration.py` creates users dynamically (it tests the creation flow itself)
- For two-user isolation tests, use both `ag3ntum_tester_a` and `ag3ntum_tester_b`

---

## Frontend Test Infrastructure

Frontend tests (`./run.sh test --ui`) always run in **dev mode** regardless of the current deployment mode. The test runner:

1. Starts the web container with `docker-compose.dev.yml` overlay (Vite dev server + node_modules)
2. Runs `npm install` if needed (copies `package.json` to `/app/` as safety net)
3. Executes `vitest run` inside the container
4. Restores the previous deployment mode (prod/dev) after tests complete

This means UI tests work correctly even after a `./run.sh build` (production mode) — the test infrastructure automatically switches the web container to dev mode.

**If UI tests fail with `ENOENT /app/package.json`**: The web container may be running in prod mode without node_modules. Run `./run.sh test --ui` again — it will recreate the container in dev mode.

---

## Writing Frontend Tests

vitest + React Testing Library + MSW:
```typescript
// tests/web_terminal_console/unit/<Component>.test.tsx
import { renderWithProviders } from '../utils/renderWithProviders';
test('renders', () => {
  renderWithProviders(<MyComponent />);
  expect(screen.getByText('expected')).toBeInTheDocument();
});
```
Setup: `setup.ts` (MSW, jest-dom, window mocks). Mocks: `mocks/handlers.ts`.

---

## Reseller/Admin Integration Tests

Backend test suites for reselling features follow a pattern of HTTP integration tests using the FastAPI test client with fixtures from `conftest.py`.

**Key fixtures** (`tests/backend/conftest.py`): `client` (TestClient), `admin_auth_headers` (admin JWT), `reseller_auth_headers` (reseller JWT), `second_reseller_auth_headers` (for IDOR tests), `db` (async session).

**Test files** (Phase 1 + Phase 2):
- `test_reseller.py` — Service-layer reseller tests (CRUD, quotas, flags, spending)
- `test_reseller_http.py` — HTTP integration tests for reseller endpoints
- `test_reseller_config_http.py` — HTTP tests for reseller config/security/ssh endpoints
- `test_reseller_metrics_http.py` — WHMCS metrics + usage export HTTP tests
- `test_admin.py` — Admin service-layer tests + platform config
- `test_admin_http.py` — Admin HTTP integration tests (reseller lifecycle, suspension)
- `test_admin_config_http.py` — Platform config GET/PUT HTTP tests
- `test_cidr_and_audit.py` — CIDR IP allowlisting + audit log tests
- `test_webhook_service.py` — Webhook service unit tests (HMAC, delivery, retry)
- `test_webhook_http.py` — Webhook CRUD HTTP integration tests + IDOR
- `test_processor_lifecycle.py` — WebhookProcessor + RetentionProcessor lifecycle
- `test_feature_flag_service.py` — FeatureFlagService unit tests (defaults, overrides, DB ops)
- `test_data_retention.py` — Data retention config + purge tests
- `test_simplify_fixes.py` — Regression tests for code quality fixes

**QA playbooks** (manual testing): `docs/plans/enable-reselling/qa-test-playbook.md` (Phase 1, 96 tests), `docs/plans/enable-reselling/qa-test-stage2.md` (Phase 2/3, 81 tests).

---

## Test Output

`logs/latest-test-results.log` (overwritten each run):
```bash
grep -A 10 "FAILED\|ERROR" logs/latest-test-results.log
```
