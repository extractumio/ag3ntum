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

## Test Output

`logs/latest-test-results.log` (overwritten each run):
```bash
grep -A 10 "FAILED\|ERROR" logs/latest-test-results.log
```
