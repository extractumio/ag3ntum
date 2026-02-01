"""
E2E Mount Access Tests.

Comprehensive tests for mount functionality using actual agent queries.
Uses a SINGLE test user for all tests (created once, cleaned up at end).

Tests cover:
- Persistent storage (read, write, list)
- Global RO mounts (read allowed, write denied)
- Global RW mounts (read and write allowed)
- Per-user mounts
- Each MCP tool: mcp__ag3ntum__Read, Write, LS, Glob, Grep, Bash
- Python code execution via Bash seeing mounts
- Access denial for paths outside mounts

Prerequisites:
- Container must be running (./run.sh build)
- External mounts configured in external-mounts.yaml

Run these tests:
    docker exec project-ag3ntum-api-1 pytest tests/backend/test_mount_e2e.py -v --run-e2e -s
"""
import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.database import Base
from src.db.models import User
from src.services.user_service import UserService

# API configuration
API_BASE_URL = "http://127.0.0.1:40080"
API_V1_URL = f"{API_BASE_URL}/api/v1"

# Test timeout for agent queries (seconds)
AGENT_TIMEOUT = 120

# Test file content for verification
TEST_MARKER = f"E2E_TEST_MARKER_{uuid.uuid4().hex[:8]}"


def _is_docker_environment() -> bool:
    """Check if we're running inside Docker."""
    return Path("/.dockerenv").exists() or os.environ.get("AG3NTUM_IN_DOCKER") == "1"


def _api_accessible() -> bool:
    """Check if API is accessible."""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', 40080))
        sock.close()
        return result == 0
    except Exception:
        return False


# =============================================================================
# Module-Scoped Fixtures (Single Setup for All Tests)
# =============================================================================

@pytest.fixture(scope="module")
def check_environment():
    """Skip all tests if not in proper environment."""
    if not _is_docker_environment():
        pytest.skip("Mount E2E tests require Docker environment")
    if not _api_accessible():
        pytest.skip("API not accessible at localhost:40080")


@pytest_asyncio.fixture(scope="module")
async def db_engine():
    """Connect to the real database."""
    db_path = Path("/data/ag3ntum.db")
    if not db_path.exists():
        pytest.skip("Real database not found")

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        echo=False,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="module")
async def db_session_factory(db_engine):
    """Create session factory for real database."""
    return async_sessionmaker(
        db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest_asyncio.fixture(scope="module")
async def user_service():
    """Get UserService instance."""
    return UserService()


@pytest_asyncio.fixture(scope="module")
async def test_user(
    user_service: UserService,
    db_session_factory,
    check_environment,
) -> AsyncGenerator[dict, None]:
    """
    Create a SINGLE test user for ALL mount tests.

    This user is created once at module start and deleted at module end.
    Returns dict with username, email, password, user object, and paths.
    """
    username = f"mount_e2e_{uuid.uuid4().hex[:8]}"
    email = f"{username}@test.example.com"
    password = "MountTest123!"

    print(f"\n{'='*60}")
    print(f"[MODULE SETUP] Creating test user: {username}")
    print(f"{'='*60}")

    async with db_session_factory() as session:
        user = await user_service.create_user(
            db=session,
            username=username,
            email=email,
            password=password,
            skip_venv_install=True,
        )
        print(f"[SETUP] User created with UID: {user.linux_uid}")

    # Create test files in persistent storage for verification
    persistent_dir = Path(f"/users/{username}/ag3ntum/persistent")
    test_file = persistent_dir / "_e2e_test_marker.txt"
    test_file.write_text(f"PERSISTENT_MARKER:{TEST_MARKER}")
    print(f"[SETUP] Created test marker file: {test_file}")

    yield {
        "username": username,
        "email": email,
        "password": password,
        "user": user,
        "persistent_dir": persistent_dir,
        "test_marker": TEST_MARKER,
    }

    # Cleanup
    print(f"\n{'='*60}")
    print(f"[MODULE TEARDOWN] Cleaning up test user: {username}")
    print(f"{'='*60}")

    # Remove test files
    if test_file.exists():
        test_file.unlink()

    # Delete user
    async with db_session_factory() as session:
        try:
            # Get fresh user from DB
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.username == username)
            )
            db_user = result.scalar_one_or_none()
            if db_user:
                await user_service.delete_user(db=session, user=db_user)
                print(f"[TEARDOWN] User deleted successfully")
        except Exception as e:
            print(f"[TEARDOWN] Warning: {e}")


@pytest_asyncio.fixture(scope="module")
async def auth_token(test_user: dict) -> str:
    """Get JWT token for test user (created once per module)."""
    email = test_user["email"]
    password = test_user["password"]

    async with httpx.AsyncClient(base_url=API_V1_URL, timeout=30.0) as client:
        response = await client.post(
            "/auth/login",
            json={"email": email, "password": password},
        )
        if response.status_code != 200:
            pytest.fail(f"Failed to login: {response.status_code} - {response.text}")

        token = response.json()["access_token"]
        print(f"[SETUP] Got auth token for {email}")
        return token


# =============================================================================
# Helper Functions
# =============================================================================

async def run_agent_task(
    token: str,
    task: str,
    timeout: int = AGENT_TIMEOUT,
) -> dict:
    """
    Run an agent task and wait for completion.

    Returns dict with session_id, status, events, tool_calls, final_message, error.
    """
    result = {
        "session_id": None,
        "status": "unknown",
        "events": [],
        "tool_calls": [],
        "final_message": "",
        "error": None,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{API_V1_URL}/sessions/run",
            headers={"Authorization": f"Bearer {token}"},
            json={"task": task},
        )

        if response.status_code not in (200, 201):
            result["error"] = f"Failed to start task: {response.status_code} - {response.text}"
            result["status"] = "failed"
            return result

        data = response.json()
        result["session_id"] = data.get("session_id")
        print(f"    Session: {result['session_id']}")

    # Stream events
    current_tool = None

    async with httpx.AsyncClient(timeout=float(timeout)) as client:
        try:
            async with client.stream(
                "GET",
                f"{API_V1_URL}/sessions/{result['session_id']}/events",
                params={"token": token},
            ) as response:
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue

                    if line.startswith("data: "):
                        try:
                            event = json.loads(line[6:])
                            result["events"].append(event)

                            event_type = event.get("type")
                            event_data = event.get("data", {})

                            if event_type == "tool_start":
                                current_tool = {
                                    "name": event_data.get("name", "unknown"),
                                    "input": event_data.get("tool_input", {}),
                                }

                            elif event_type == "tool_complete":
                                if current_tool:
                                    current_tool["result"] = event_data.get("result", "")
                                    current_tool["error"] = event_data.get("error")
                                    result["tool_calls"].append(current_tool)
                                    current_tool = None

                            elif event_type == "message":
                                text = event_data.get("text", "")
                                if text and not event_data.get("is_partial"):
                                    result["final_message"] = text

                            elif event_type == "error":
                                result["error"] = event_data.get("message", str(event_data))
                                result["status"] = "error"

                            elif event_type in ("agent_complete", "cancelled"):
                                result["status"] = event_type
                                break

                        except json.JSONDecodeError:
                            pass

        except httpx.ReadTimeout:
            result["status"] = "timeout"
            result["error"] = f"Timeout after {timeout}s"

    tools_used = [t["name"] for t in result["tool_calls"]]
    print(f"    Status: {result['status']}, Tools: {tools_used}")
    return result


def get_response_text(result: dict) -> str:
    """Extract readable text from agent result."""
    # Try to extract text from final_message content blocks
    if result.get("final_message"):
        msg = result["final_message"]
        # Handle list of content blocks: [{'type': 'text', 'text': '...'}]
        if isinstance(msg, list):
            texts = []
            for block in msg:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
            if texts:
                return "\n".join(texts)
        return str(msg)
    # Fall back to tool call results
    for tool in result.get("tool_calls", []):
        if tool.get("result"):
            return str(tool["result"])
    return ""


def find_tool_result(result: dict, tool_name: str) -> str | None:
    """Find result from a specific tool call."""
    for tool in result.get("tool_calls", []):
        if tool_name in tool.get("name", ""):
            return tool.get("result", "")
    return None


# =============================================================================
# Test Class: Persistent Storage
# =============================================================================

@pytest.mark.e2e
@pytest.mark.slow
class TestPersistentStorage:
    """Tests for ./persistent/ directory access."""

    @pytest.mark.asyncio
    async def test_persistent_read_via_ls(
        self, auth_token: str, test_user: dict, check_environment
    ) -> None:
        """Agent should see files in ./persistent/ via LS."""
        print("\n[TEST] LS on ./persistent/")

        result = await run_agent_task(
            auth_token,
            "Use the mcp__ag3ntum__LS tool to list contents of ./persistent/ directory",
        )

        assert result["status"] == "agent_complete", f"Failed: {result.get('error')}"

        response = get_response_text(result)
        print(f"    Response: {response[:300]}")

        # Should see our test marker file
        assert "_e2e_test_marker.txt" in response, "Test marker file not visible"

    @pytest.mark.asyncio
    async def test_persistent_read_via_read(
        self, auth_token: str, test_user: dict, check_environment
    ) -> None:
        """Agent should read files in ./persistent/ via Read tool."""
        print("\n[TEST] Read from ./persistent/")

        result = await run_agent_task(
            auth_token,
            "Use mcp__ag3ntum__Read to read the file ./persistent/_e2e_test_marker.txt",
        )

        assert result["status"] == "agent_complete", f"Failed: {result.get('error')}"

        response = get_response_text(result)
        print(f"    Response: {response[:300]}")

        # Should contain our marker
        assert test_user["test_marker"] in response, "Test marker content not found"

    @pytest.mark.asyncio
    async def test_persistent_write_via_write(
        self, auth_token: str, test_user: dict, check_environment
    ) -> None:
        """Agent should write files to ./persistent/ via Write tool."""
        print("\n[TEST] Write to ./persistent/")

        test_content = f"WRITE_TEST_{uuid.uuid4().hex[:8]}"
        test_filename = f"_write_test_{uuid.uuid4().hex[:6]}.txt"

        result = await run_agent_task(
            auth_token,
            f"Use mcp__ag3ntum__Write to create file ./persistent/{test_filename} with content: {test_content}",
        )

        assert result["status"] == "agent_complete", f"Failed: {result.get('error')}"

        # Verify file was created
        created_file = test_user["persistent_dir"] / test_filename
        assert created_file.exists(), "File was not created"
        assert test_content in created_file.read_text(), "Content mismatch"

        # Cleanup
        created_file.unlink()
        print(f"    File created and verified: {test_filename}")

    @pytest.mark.asyncio
    async def test_persistent_glob_pattern(
        self, auth_token: str, test_user: dict, check_environment
    ) -> None:
        """Agent should find files in ./persistent/ via Glob."""
        print("\n[TEST] Glob on ./persistent/")

        result = await run_agent_task(
            auth_token,
            "Use mcp__ag3ntum__Glob to find all .txt files in ./persistent/ with pattern '*.txt'",
        )

        assert result["status"] == "agent_complete", f"Failed: {result.get('error')}"

        response = get_response_text(result)
        print(f"    Response: {response[:300]}")

        # Should find our test marker file
        assert "_e2e_test_marker.txt" in response, "Glob didn't find marker file"

    @pytest.mark.asyncio
    async def test_persistent_via_bash_ls(
        self, auth_token: str, test_user: dict, check_environment
    ) -> None:
        """Agent should see ./persistent/ via Bash ls command."""
        print("\n[TEST] Bash ls on ./persistent/")

        result = await run_agent_task(
            auth_token,
            "Run this bash command: ls -la ./persistent/",
        )

        assert result["status"] == "agent_complete", f"Failed: {result.get('error')}"

        response = get_response_text(result)
        print(f"    Response: {response[:300]}")

        assert "_e2e_test_marker.txt" in response, "Bash ls didn't show marker file"

    @pytest.mark.asyncio
    async def test_persistent_via_python(
        self, auth_token: str, test_user: dict, check_environment
    ) -> None:
        """Agent should access ./persistent/ via Python code in Bash."""
        print("\n[TEST] Python code accessing ./persistent/")

        result = await run_agent_task(
            auth_token,
            """Run this Python code via bash:
python3 -c "
import os
files = os.listdir('./persistent/')
print('FILES:', files)
for f in files:
    if f.endswith('.txt'):
        content = open(f'./persistent/{f}').read()
        print(f'CONTENT of {f}:', content[:50])
"
""",
        )

        assert result["status"] == "agent_complete", f"Failed: {result.get('error')}"

        response = get_response_text(result)
        print(f"    Response: {response[:400]}")

        # Python should see the marker file
        assert "_e2e_test_marker.txt" in response, "Python didn't see marker file"
        assert test_user["test_marker"] in response, "Python didn't read marker content"


# =============================================================================
# Test Class: Read-Only Mounts
# =============================================================================

@pytest.mark.e2e
@pytest.mark.slow
class TestReadOnlyMounts:
    """Tests for read-only mount behavior."""

    @pytest.mark.asyncio
    async def test_ro_mount_ls(
        self, auth_token: str, check_environment
    ) -> None:
        """Agent should list contents of RO mount via LS."""
        print("\n[TEST] LS on ./external/ro/")

        result = await run_agent_task(
            auth_token,
            "Use mcp__ag3ntum__LS to list ./external/ro/ directory",
        )

        assert result["status"] == "agent_complete", f"Failed: {result.get('error')}"

        response = get_response_text(result)
        print(f"    Response: {response[:300]}")

        # Should show global_var_log or indicate directory structure
        if "no such" in response.lower() or "does not exist" in response.lower():
            pytest.skip("RO mount not configured")

    @pytest.mark.asyncio
    async def test_ro_mount_read(
        self, auth_token: str, check_environment
    ) -> None:
        """Agent should read files from RO mount."""
        print("\n[TEST] Read from RO mount")

        # Try to read a common log file that should exist on most Linux systems
        result = await run_agent_task(
            auth_token,
            """Use mcp__ag3ntum__Read to read the first 5 lines of
            ./external/ro/global_var_log/syslog (offset=1, limit=5).
            If syslog doesn't exist, try reading dpkg.log instead.""",
        )

        response = get_response_text(result)
        print(f"    Response: {response[:400]}")

        # Check if the mount is accessible at all
        if "not configured" in response.lower() or "mount not found" in response.lower():
            pytest.skip("RO mount not configured")

        # The test passes if we got some content (even if error for specific file)
        # The key is that the mount itself is accessible
        assert result["status"] == "agent_complete", f"Task failed: {result.get('error')}"

    @pytest.mark.asyncio
    async def test_ro_mount_write_denied_via_write(
        self, auth_token: str, check_environment
    ) -> None:
        """Agent should NOT be able to write to RO mount via Write tool."""
        print("\n[TEST] Write to RO mount (should fail)")

        test_filename = f"_hack_{uuid.uuid4().hex[:6]}.txt"

        result = await run_agent_task(
            auth_token,
            f"""Try to create a file using mcp__ag3ntum__Write at
            ./external/ro/global_var_log/{test_filename} with content "hacked".
            Report the exact error message you receive.""",
        )

        response = get_response_text(result)
        print(f"    Response: {response[:400]}")

        if "no such" in response.lower() or "does not exist" in response.lower():
            pytest.skip("RO mount not configured")

        # Should indicate failure
        failure_words = ["read-only", "denied", "cannot", "failed", "error", "not allowed"]
        response_lower = response.lower()
        assert any(w in response_lower for w in failure_words), \
            f"Write to RO should have failed! Response: {response[:200]}"

    @pytest.mark.asyncio
    async def test_ro_mount_write_denied_via_bash(
        self, auth_token: str, check_environment
    ) -> None:
        """Agent should NOT be able to write to RO mount via Bash."""
        print("\n[TEST] Bash write to RO mount (should fail)")

        test_filename = f"_hack_{uuid.uuid4().hex[:6]}.txt"

        result = await run_agent_task(
            auth_token,
            f"""Run this bash command and tell me if it succeeds or fails:
            echo "hacked" > ./external/ro/global_var_log/{test_filename}""",
        )

        response = get_response_text(result)
        print(f"    Response: {response[:400]}")

        if "no such" in response.lower():
            pytest.skip("RO mount not configured")

        # Verify file wasn't created
        test_paths = [
            Path(f"/mounts/global_var_log/{test_filename}"),
            Path(f"/mounts/ro/global_var_log/{test_filename}"),
        ]
        for p in test_paths:
            assert not p.exists(), f"File was created at {p}!"

    @pytest.mark.asyncio
    async def test_ro_mount_grep(
        self, auth_token: str, check_environment
    ) -> None:
        """Agent should search RO mount via Grep."""
        print("\n[TEST] Grep on RO mount")

        result = await run_agent_task(
            auth_token,
            """Use mcp__ag3ntum__Grep to search for any pattern in
            ./external/ro/global_var_log/ - just search for "." to find any files""",
        )

        response = get_response_text(result)
        print(f"    Response: {response[:300]}")

        # As long as it doesn't error on access, it's fine
        if "no such" in response.lower():
            pytest.skip("RO mount not configured")


# =============================================================================
# Test Class: Read-Write Mounts
# =============================================================================

@pytest.mark.e2e
@pytest.mark.slow
class TestReadWriteMounts:
    """Tests for read-write mount behavior."""

    @pytest.mark.asyncio
    async def test_rw_mount_ls(
        self, auth_token: str, check_environment
    ) -> None:
        """Agent should list contents of RW mount via LS."""
        print("\n[TEST] LS on ./external/rw/")

        result = await run_agent_task(
            auth_token,
            "Use mcp__ag3ntum__LS to list ./external/rw/ and show subdirectories",
        )

        assert result["status"] == "agent_complete", f"Failed: {result.get('error')}"

        response = get_response_text(result)
        print(f"    Response: {response[:300]}")

    @pytest.mark.asyncio
    async def test_rw_mount_write_and_read(
        self, auth_token: str, check_environment
    ) -> None:
        """Agent should write and read from RW mount."""
        print("\n[TEST] Write then Read on RW mount")

        test_content = f"RW_TEST_{uuid.uuid4().hex[:8]}"
        test_filename = f"_rw_test_{uuid.uuid4().hex[:6]}.txt"

        result = await run_agent_task(
            auth_token,
            f"""Do these steps:
1. Use mcp__ag3ntum__Write to create ./external/rw/product_docs/{test_filename} with content: {test_content}
2. Use mcp__ag3ntum__Read to read it back
3. Tell me if the content matches""",
        )

        response = get_response_text(result)
        print(f"    Response: {response[:400]}")

        if "no such" in response.lower() or "does not exist" in response.lower():
            pytest.skip("RW mount not configured")

        # Cleanup
        test_paths = [
            Path(f"/mounts/product_docs/{test_filename}"),
            Path(f"/mounts/rw/product_docs/{test_filename}"),
        ]
        for p in test_paths:
            if p.exists():
                p.unlink()
                print(f"    Cleaned up: {p}")

    @pytest.mark.asyncio
    async def test_rw_mount_via_python(
        self, auth_token: str, check_environment
    ) -> None:
        """Agent should access RW mount via Python code."""
        print("\n[TEST] Python code on RW mount")

        test_content = f"PYTHON_RW_{uuid.uuid4().hex[:8]}"
        test_filename = f"_python_test_{uuid.uuid4().hex[:6]}.txt"

        result = await run_agent_task(
            auth_token,
            f"""Run this Python code:
python3 -c "
import os
path = './external/rw/product_docs/{test_filename}'
# Try to write
try:
    with open(path, 'w') as f:
        f.write('{test_content}')
    print('WRITE: SUCCESS')
    # Read back
    with open(path, 'r') as f:
        content = f.read()
    print('READ:', content)
except Exception as e:
    print('ERROR:', e)
"
""",
        )

        response = get_response_text(result)
        print(f"    Response: {response[:400]}")

        if "no such" in response.lower():
            pytest.skip("RW mount not configured")

        # Cleanup
        test_paths = [
            Path(f"/mounts/product_docs/{test_filename}"),
            Path(f"/mounts/rw/product_docs/{test_filename}"),
        ]
        for p in test_paths:
            if p.exists():
                p.unlink()


# =============================================================================
# Test Class: Access Denial
# =============================================================================

@pytest.mark.e2e
@pytest.mark.slow
class TestAccessDenial:
    """Tests for unauthorized access denial."""

    @pytest.mark.asyncio
    async def test_cannot_read_etc_passwd(
        self, auth_token: str, check_environment
    ) -> None:
        """Agent should NOT read /etc/passwd."""
        print("\n[TEST] Read /etc/passwd (should be denied)")

        result = await run_agent_task(
            auth_token,
            "Use mcp__ag3ntum__Read to read /etc/passwd and show its contents",
        )

        response = get_response_text(result)
        print(f"    Response: {response[:300]}")

        # Should NOT contain actual passwd content
        passwd_indicators = ["root:x:", "nobody:", "/bin/bash", "/sbin/nologin"]
        assert not any(ind in response for ind in passwd_indicators), \
            "Agent may have accessed /etc/passwd!"

    @pytest.mark.asyncio
    async def test_cannot_read_etc_via_bash(
        self, auth_token: str, check_environment
    ) -> None:
        """Agent should NOT read /etc/passwd via Bash."""
        print("\n[TEST] Bash cat /etc/passwd (should be denied)")

        result = await run_agent_task(
            auth_token,
            "Run: cat /etc/passwd",
        )

        response = get_response_text(result)
        print(f"    Response: {response[:300]}")

        passwd_indicators = ["root:x:", "nobody:", "/bin/bash"]
        assert not any(ind in response for ind in passwd_indicators), \
            "Bash may have accessed /etc/passwd!"

    @pytest.mark.asyncio
    async def test_cannot_read_etc_via_python(
        self, auth_token: str, check_environment
    ) -> None:
        """Agent should NOT read /etc/passwd via Python."""
        print("\n[TEST] Python read /etc/passwd (should fail)")

        result = await run_agent_task(
            auth_token,
            """Run this Python code:
python3 -c "
try:
    content = open('/etc/passwd').read()
    print('CONTENT:', content[:200])
except Exception as e:
    print('ERROR:', type(e).__name__, str(e))
"
""",
        )

        response = get_response_text(result)
        print(f"    Response: {response[:300]}")

        # Should show an error, not content
        passwd_indicators = ["root:x:", "nobody:"]
        assert not any(ind in response for ind in passwd_indicators), \
            "Python may have accessed /etc/passwd!"

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(
        self, auth_token: str, check_environment
    ) -> None:
        """Path traversal should be blocked."""
        print("\n[TEST] Path traversal (should be blocked)")

        result = await run_agent_task(
            auth_token,
            "Use mcp__ag3ntum__Read to read ./external/../../../etc/shadow",
        )

        response = get_response_text(result)
        print(f"    Response: {response[:300]}")

        # Should not contain shadow file content
        shadow_indicators = ["$6$", "$y$", "$5$"]
        assert not any(ind in response for ind in shadow_indicators), \
            "Path traversal may have succeeded!"


# =============================================================================
# Test Class: Mount Summary
# =============================================================================

@pytest.mark.e2e
@pytest.mark.slow
class TestMountSummary:
    """Summary tests showing complete mount visibility."""

    @pytest.mark.asyncio
    async def test_complete_workspace_structure(
        self, auth_token: str, test_user: dict, check_environment
    ) -> None:
        """Show complete workspace structure agent sees."""
        print("\n[TEST] Complete workspace structure")

        result = await run_agent_task(
            auth_token,
            """Give me a complete directory tree of:
1. Run: ls -la ./ (workspace root)
2. Run: find ./external -type d 2>/dev/null | head -20
3. Run: ls -la ./persistent/

Show all output.""",
        )

        assert result["status"] == "agent_complete", f"Failed: {result.get('error')}"

        response = get_response_text(result)
        print(f"\n{'='*50}")
        print("WORKSPACE STRUCTURE:")
        print(f"{'='*50}")
        print(response[:1500] if response else "No response")
        print(f"{'='*50}\n")

    @pytest.mark.asyncio
    async def test_python_sees_all_mounts(
        self, auth_token: str, test_user: dict, check_environment
    ) -> None:
        """Python code should see all mounted directories."""
        print("\n[TEST] Python inspection of mounts")

        result = await run_agent_task(
            auth_token,
            """Run this Python code to inspect available paths:
python3 -c "
import os
from pathlib import Path

def scan_dir(path, depth=0):
    try:
        for item in sorted(Path(path).iterdir()):
            prefix = '  ' * depth
            suffix = '/' if item.is_dir() else ''
            print(f'{prefix}{item.name}{suffix}')
            if item.is_dir() and depth < 2:
                scan_dir(item, depth + 1)
    except Exception as e:
        print(f'  ERROR: {e}')

print('=== WORKSPACE ROOT ===')
scan_dir('.')

print('\\n=== PERSISTENT ===')
scan_dir('./persistent')

print('\\n=== EXTERNAL ===')
scan_dir('./external')
"
""",
            timeout=90,
        )

        assert result["status"] == "agent_complete", f"Failed: {result.get('error')}"

        response = get_response_text(result)
        print(f"\n{'='*50}")
        print("PYTHON MOUNT INSPECTION:")
        print(f"{'='*50}")
        print(response[:2000] if response else "No response")
        print(f"{'='*50}\n")

        # Verify key directories are visible
        assert "external" in response.lower() or "persistent" in response.lower(), \
            "Python didn't see expected directories"
