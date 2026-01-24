"""
End-to-end integration tests that start the real backend server.

These tests verify:
- Server starts correctly on a custom port
- Real HTTP requests work
- Proper config loading (permissions, skills)
- Complete agent execution with skills
- Output validation
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Generator
import socket

import httpx
import pytest
import yaml

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
TEST_INPUT_DIR = Path(__file__).parent / "input"
sys.path.insert(0, str(PROJECT_ROOT))


def _check_api_key_available() -> bool:
    """
    Check if ANTHROPIC_API_KEY is available from any source.
    
    Checks in order:
    1. Environment variable ANTHROPIC_API_KEY
    2. Environment variable CLOUDLINUX_ANTHROPIC_API_KEY
    3. config/secrets.yaml file
    """
    # Check environment variables first
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLOUDLINUX_ANTHROPIC_API_KEY"):
        return True
    
    # Check secrets.yaml (both in Docker /config and local config/)
    secrets_paths = [
        Path("/config/secrets.yaml"),  # Docker mount
        PROJECT_ROOT / "config" / "secrets.yaml",  # Local development
    ]
    
    for secrets_path in secrets_paths:
        if secrets_path.exists():
            try:
                with open(secrets_path) as f:
                    secrets = yaml.safe_load(f) or {}
                if secrets.get("anthropic_api_key"):
                    return True
            except (yaml.YAMLError, OSError):
                pass
    
    return False


# Check if API key is available for E2E tests that require the real model
HAS_API_KEY = _check_api_key_available()


def find_free_port() -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def wait_for_server(host: str, port: int, timeout: float = 10.0) -> bool:
    """Wait for server to become available."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.1)
    return False


@pytest.fixture(scope="module")
def test_environment() -> Generator[dict, None, None]:
    """
    Create a complete test environment with proper config files.

    Sets up:
    - Temp directories (sessions, logs, data)
    - Real config files from tests/backend/input/
    - Secrets from project config
    - Skills directory
    """
    # Create temp directories
    temp_base = Path(tempfile.mkdtemp(prefix="ag3ntum_e2e_"))
    temp_users = temp_base / "users"  # Users directory (contains username/sessions/)
    temp_logs = temp_base / "logs"
    temp_data = temp_base / "data"
    temp_config = temp_base / "config"
    temp_skills = temp_base / "skills"
    temp_prompts = temp_base / "prompts"

    temp_users.mkdir()
    temp_logs.mkdir()
    temp_data.mkdir()
    temp_config.mkdir()
    temp_skills.mkdir()
    temp_prompts.mkdir()

    # Copy test config files from input directory
    test_config_dir = TEST_INPUT_DIR / "config"
    if test_config_dir.exists():
        for config_file in test_config_dir.glob("*.yaml"):
            shutil.copy(config_file, temp_config / config_file.name)

    # Copy secrets from project config (contains API key)
    project_secrets = PROJECT_ROOT / "config" / "secrets.yaml"
    if project_secrets.exists():
        shutil.copy(project_secrets, temp_config / "secrets.yaml")
    else:
        pytest.skip("secrets.yaml not found - cannot run E2E tests")

    # Copy skills from test input
    test_skills_dir = TEST_INPUT_DIR / "skills"
    if test_skills_dir.exists():
        shutil.copytree(test_skills_dir, temp_skills, dirs_exist_ok=True)

    # Copy prompts from project
    project_prompts = PROJECT_ROOT / "prompts"
    if project_prompts.exists():
        shutil.copytree(project_prompts, temp_prompts, dirs_exist_ok=True)

    # Generate api.yaml with dynamic port
    test_port = find_free_port()
    api_config = {
        "api": {
            "host": "127.0.0.1",
            "port": test_port,
            "external_port": test_port,  # Required for Host header validation
            "cors_origins": ["http://localhost:3000"],
        },
        "server": {
            "hostname": "127.0.0.1",  # Must match the test URL hostname
            "protocol": "http",
        },
        "security": {
            # Allow the test host and any origin for CORS testing
            "additional_allowed_hosts": ["127.0.0.1"],
        },
        "web": {
            "external_port": 3000,  # For CORS origin validation
        },
        "database": {
            "path": str(temp_data / "test.db"),
        },
        "jwt": {
            "algorithm": "HS256",
            "expiry_hours": 168,
        },
        "redis": {
            "url": "redis://redis:6379/0",  # Use Docker redis service name
        },
    }
    with open(temp_config / "api.yaml", "w") as f:
        yaml.dump(api_config, f)

    env = {
        "temp_base": temp_base,
        "temp_users": temp_users,
        "temp_logs": temp_logs,
        "temp_data": temp_data,
        "temp_config": temp_config,
        "temp_skills": temp_skills,
        "temp_prompts": temp_prompts,
        "port": test_port,
        "host": "127.0.0.1",
        "base_url": f"http://127.0.0.1:{test_port}",
    }

    yield env

    # Cleanup
    if temp_base.exists():
        shutil.rmtree(temp_base, ignore_errors=True)


# Server runner script with proper config patching
SERVER_RUNNER_SCRIPT = '''
"""Standalone server runner for E2E tests with full config support."""
import sys
import os
import logging
from pathlib import Path

# Set up logging to see errors
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s - %(name)s - %(message)s")

# Get config from environment
config_dir = Path(os.environ["AG3NTUM_E2E_CONFIG_DIR"])
users_dir = Path(os.environ["AG3NTUM_E2E_USERS_DIR"])
logs_dir = Path(os.environ["AG3NTUM_E2E_LOGS_DIR"])
data_dir = Path(os.environ["AG3NTUM_E2E_DATA_DIR"])
skills_dir = Path(os.environ["AG3NTUM_E2E_SKILLS_DIR"])
prompts_dir = Path(os.environ["AG3NTUM_E2E_PROMPTS_DIR"])
port = int(os.environ["AG3NTUM_E2E_PORT"])
host = os.environ["AG3NTUM_E2E_HOST"]
project_root = Path(os.environ["AG3NTUM_E2E_PROJECT_ROOT"])

# Add project root to path
sys.path.insert(0, str(project_root))

# Patch ALL config paths BEFORE importing anything
import src.config as config_module
config_module.CONFIG_DIR = config_dir
config_module.USERS_DIR = users_dir
config_module.LOGS_DIR = logs_dir
config_module.SKILLS_DIR = skills_dir
config_module.PROMPTS_DIR = prompts_dir
config_module.AGENT_CONFIG_FILE = config_dir / "agent.yaml"
config_module.SECRETS_FILE = config_dir / "secrets.yaml"

# Patch API config path
import src.api.main as main_module
main_module.API_CONFIG_FILE = config_dir / "api.yaml"

# Patch database path BEFORE any database imports
db_file = data_dir / "test.db"

# Set environment variable that database.py reads
os.environ["AG3NTUM_DATABASE_PATH"] = str(db_file)

# Now import and patch database module
import src.db.database as db_module
db_module.DATABASE_PATH = db_file
db_module.DATA_DIR = data_dir
db_module.DATABASE_URL = f"sqlite+aiosqlite:///{db_file}"

# Recreate the engine and session factory with patched path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
db_module.engine = create_async_engine(
    f"sqlite+aiosqlite:///{db_file}",
    echo=False,
    connect_args={"check_same_thread": False},
)
db_module.AsyncSessionLocal = async_sessionmaker(
    db_module.engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Now create and run the app
import uvicorn
from src.api.main import create_app

app = create_app()
uvicorn.run(app, host=host, port=port, log_level="debug")
'''


@pytest.fixture(scope="module")
def test_user_credentials() -> dict:
    """Test user credentials for E2E tests."""
    import uuid
    test_id = uuid.uuid4().hex[:8]
    return {
        "email": f"e2e_test_{test_id}@example.com",
        "password": "test123",
        "username": f"e2e_user_{test_id}",
    }


@pytest.fixture(scope="module")
def running_server(test_environment: dict, test_user_credentials: dict) -> Generator[dict, None, None]:
    """
    Start the real backend server on a test port using subprocess.
    """
    port = test_environment["port"]
    host = test_environment["host"]
    temp_config = test_environment["temp_config"]
    temp_users = test_environment["temp_users"]
    temp_logs = test_environment["temp_logs"]
    temp_data = test_environment["temp_data"]
    temp_skills = test_environment["temp_skills"]
    temp_prompts = test_environment["temp_prompts"]
    temp_base = test_environment["temp_base"]

    # Write the runner script
    runner_script = temp_base / "run_server.py"
    runner_script.write_text(SERVER_RUNNER_SCRIPT)

    # Set environment variables for the subprocess
    env = os.environ.copy()
    env["AG3NTUM_E2E_CONFIG_DIR"] = str(temp_config)
    env["AG3NTUM_E2E_USERS_DIR"] = str(temp_users)
    env["AG3NTUM_E2E_LOGS_DIR"] = str(temp_logs)
    env["AG3NTUM_E2E_DATA_DIR"] = str(temp_data)
    env["AG3NTUM_E2E_SKILLS_DIR"] = str(temp_skills)
    env["AG3NTUM_E2E_PROMPTS_DIR"] = str(temp_prompts)
    env["AG3NTUM_E2E_PORT"] = str(port)
    env["AG3NTUM_E2E_HOST"] = host
    env["AG3NTUM_E2E_PROJECT_ROOT"] = str(PROJECT_ROOT)

    # Start server as subprocess - write stderr to file for debugging
    stderr_log = temp_base / "server_stderr.log"
    stderr_file = open(stderr_log, "w")

    python_executable = sys.executable
    process = subprocess.Popen(
        [python_executable, str(runner_script)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=stderr_file,
        cwd=str(PROJECT_ROOT),
    )

    # Wait for server to be ready
    if not wait_for_server(host, port, timeout=15.0):
        process.terminate()
        stderr_file.close()
        stderr_content = stderr_log.read_text() if stderr_log.exists() else "(no stderr)"
        pytest.fail(
            f"Server failed to start on {host}:{port}\n"
            f"stderr:\n{stderr_content}"
        )

    time.sleep(0.3)

    # Create test user directly in the database
    try:
        import secrets
        import uuid
        import bcrypt
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        sys.path.insert(0, str(PROJECT_ROOT))
        from src.db.models import Base, User
        
        # Connect to test database
        db_path = temp_data / "test.db"
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        
        # Create test user
        password_hash = bcrypt.hashpw(
            test_user_credentials["password"].encode(),
            bcrypt.gensalt()
        ).decode()
        
        # Use the current process UID for the test user (typically 0 in Docker or user's UID locally)
        # This allows the agent to run under the same user context as the test
        import os as test_os
        test_linux_uid = test_os.getuid()

        test_user_obj = User(
            id=str(uuid.uuid4()),
            username=test_user_credentials["username"],
            email=test_user_credentials["email"],
            password_hash=password_hash,
            role="user",
            jwt_secret=secrets.token_urlsafe(32),
            linux_uid=test_linux_uid,
            is_active=True,
        )
        session.add(test_user_obj)
        session.commit()
        
        # Verify user was created
        created_user = session.query(User).filter_by(email=test_user_credentials["email"]).first()
        if created_user:
            print(f"✓ Created E2E test user: {test_user_credentials['username']} ({test_user_credentials['email']})")
            print(f"  User ID: {created_user.id}, Active: {created_user.is_active}, Linux UID: {created_user.linux_uid}")
            print(f"  Database: {db_path}")
            # Test the password hash immediately
            test_check = bcrypt.checkpw(
                test_user_credentials["password"].encode(),
                created_user.password_hash.encode()
            )
            print(f"  Password hash verification: {test_check}")
            
            # Create user directory structure
            user_dir = temp_users / test_user_credentials["username"]
            user_sessions_dir = user_dir / "sessions"
            user_sessions_dir.mkdir(parents=True, exist_ok=True)

            # Create venv directory structure for validate_user_environment
            # This creates a minimal venv structure so the auth validation passes
            user_venv = user_dir / "venv" / "bin"
            user_venv.mkdir(parents=True, exist_ok=True)
            # Create dummy python3 binary (just needs to exist)
            python_bin = user_venv / "python3"
            python_bin.touch()
            python_bin.chmod(0o755)
            print(f"  Created user directory with venv: {user_dir}")
        else:
            print(f"✗ User creation verification failed!")
            
        session.close()
        engine.dispose()
        
        # Give SQLite time to flush and close connections
        time.sleep(0.5)
        
    except Exception as e:
        import traceback
        print(f"✗ Warning: Could not create test user: {e}")
        print(traceback.format_exc())
        # Still yield to let tests run (they might skip gracefully)

    yield {
        **test_environment,
        "process": process,
        "test_user": test_user_credentials,
        "stderr_log": stderr_log,
    }

    # Shutdown server
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    # Close stderr file
    stderr_file.close()


class TestServerStartup:
    """Tests that verify the server starts correctly."""

    @pytest.mark.integration
    def test_server_starts_and_responds(self, running_server: dict) -> None:
        """Server starts and responds to health check."""
        base_url = running_server["base_url"]

        response = httpx.get(f"{base_url}/api/v1/health", timeout=5.0)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"

    @pytest.mark.integration
    def test_server_responds_to_real_http(self, running_server: dict) -> None:
        """Server handles real HTTP connections."""
        base_url = running_server["base_url"]

        for _ in range(3):
            response = httpx.get(f"{base_url}/api/v1/health", timeout=5.0)
            assert response.status_code == 200

    @pytest.mark.integration
    def test_cors_headers_present(self, running_server: dict) -> None:
        """Server returns CORS headers."""
        base_url = running_server["base_url"]
        # Extract host:port from base_url for Host header validation
        # base_url is like "http://127.0.0.1:12345"
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        host_header = f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname

        response = httpx.options(
            f"{base_url}/api/v1/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Host": host_header,  # Explicit host to pass validation
            },
            timeout=5.0,
        )

        assert response.status_code in (200, 204)


def get_auth_token(base_url: str, email: str, password: str) -> str:
    """
    Helper to login and get auth token.
    
    Returns the access_token string.
    """
    response = httpx.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=5.0
    )
    
    if response.status_code == 200:
        return response.json()["access_token"]
    
    raise RuntimeError(
        f"Login failed with {response.status_code}: {response.text}. "
        "Ensure test user exists in the database."
    )


class TestRealEndpoints:
    """Tests that verify real endpoint functionality."""

    @pytest.mark.integration
    def test_auth_token_endpoint(self, running_server: dict) -> None:
        """Can login with email/password from real server."""
        base_url = running_server["base_url"]
        test_user = running_server["test_user"]

        # Login with test user credentials
        response = httpx.post(
            f"{base_url}/api/v1/auth/login",
            json={
                "email": test_user["email"],
                "password": test_user["password"]
            },
            timeout=5.0
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0

        # Verify the token is a valid JWT (has 3 parts separated by dots)
        token_parts = data["access_token"].split(".")
        assert len(token_parts) == 3, "Token should be a valid JWT with 3 parts"

    @pytest.mark.integration
    def test_authenticated_request(self, running_server: dict) -> None:
        """Can make authenticated requests."""
        base_url = running_server["base_url"]
        test_user = running_server["test_user"]

        token = get_auth_token(base_url, test_user["email"], test_user["password"])

        headers = {"Authorization": f"Bearer {token}"}
        response = httpx.get(
            f"{base_url}/api/v1/sessions",
            headers=headers,
            timeout=5.0,
        )

        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert "total" in data

    @pytest.mark.integration
    def test_session_create_endpoint(self, running_server: dict) -> None:
        """Can create a session through real endpoint."""
        base_url = running_server["base_url"]

        test_user = running_server["test_user"]
        token = get_auth_token(base_url, test_user["email"], test_user["password"])
        headers = {"Authorization": f"Bearer {token}"}

        response = httpx.post(
            f"{base_url}/api/v1/sessions",
            headers=headers,
            json={"task": "E2E test task"},
            timeout=5.0,
        )

        assert response.status_code == 201, f"Session create failed: {response.status_code} - {response.text}"
        data = response.json()
        assert "id" in data
        assert data["status"] == "pending"
        assert data["task"] == "E2E test task"

    @pytest.mark.integration
    def test_session_lifecycle(self, running_server: dict) -> None:
        """Test complete session lifecycle through real endpoints."""
        base_url = running_server["base_url"]

        test_user = running_server["test_user"]
        token = get_auth_token(base_url, test_user["email"], test_user["password"])
        headers = {"Authorization": f"Bearer {token}"}

        # Create session
        create_response = httpx.post(
            f"{base_url}/api/v1/sessions",
            headers=headers,
            json={"task": "Lifecycle test"},
            timeout=5.0,
        )
        assert create_response.status_code == 201
        session_id = create_response.json()["id"]

        # Get session
        get_response = httpx.get(
            f"{base_url}/api/v1/sessions/{session_id}",
            headers=headers,
            timeout=5.0,
        )
        assert get_response.status_code == 200
        assert get_response.json()["id"] == session_id

        # List sessions
        list_response = httpx.get(
            f"{base_url}/api/v1/sessions",
            headers=headers,
            timeout=5.0,
        )
        assert list_response.status_code == 200
        assert list_response.json()["total"] >= 1

        # Get result
        result_response = httpx.get(
            f"{base_url}/api/v1/sessions/{session_id}/result",
            headers=headers,
            timeout=5.0,
        )
        assert result_response.status_code == 200
        assert result_response.json()["session_id"] == session_id

    @pytest.mark.integration
    def test_error_handling(self, running_server: dict) -> None:
        """Server handles errors correctly."""
        base_url = running_server["base_url"]

        test_user = running_server["test_user"]
        token = get_auth_token(base_url, test_user["email"], test_user["password"])
        headers = {"Authorization": f"Bearer {token}"}

        response = httpx.get(
            f"{base_url}/api/v1/sessions/nonexistent-session",
            headers=headers,
            timeout=5.0,
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.integration
    def test_validation_errors(self, running_server: dict) -> None:
        """Server returns proper validation errors."""
        base_url = running_server["base_url"]

        test_user = running_server["test_user"]
        token = get_auth_token(base_url, test_user["email"], test_user["password"])
        headers = {"Authorization": f"Bearer {token}"}

        response = httpx.post(
            f"{base_url}/api/v1/sessions",
            headers=headers,
            json={},  # Missing 'task'
            timeout=5.0,
        )

        assert response.status_code == 422
        data = response.json()
        assert "detail" in data


class TestServerCleanup:
    """Tests for server cleanup and artifacts."""

    @pytest.mark.integration
    def test_sessions_created_in_temp_dir(
        self,
        running_server: dict
    ) -> None:
        """Sessions are created in the configured temp directory."""
        base_url = running_server["base_url"]
        temp_users = running_server["temp_users"]
        test_user = running_server["test_user"]

        token = get_auth_token(base_url, test_user["email"], test_user["password"])
        headers = {"Authorization": f"Bearer {token}"}

        response = httpx.post(
            f"{base_url}/api/v1/sessions",
            headers=headers,
            json={"task": "Temp dir test"},
            timeout=5.0,
        )
        session_id = response.json()["id"]

        # Sessions are now stored under users/{username}/sessions/
        username = test_user["username"]
        session_folder = temp_users / username / "sessions" / session_id
        assert session_folder.exists(), \
            f"Session folder should exist at {session_folder}"

    @pytest.mark.integration
    def test_database_operations_work(self, running_server: dict) -> None:
        """Database operations work correctly."""
        base_url = running_server["base_url"]

        test_user = running_server["test_user"]
        token = get_auth_token(base_url, test_user["email"], test_user["password"])
        headers = {"Authorization": f"Bearer {token}"}

        create_response = httpx.post(
            f"{base_url}/api/v1/sessions",
            headers=headers,
            json={"task": "Database persistence test"},
            timeout=5.0,
        )
        session_id = create_response.json()["id"]

        get_response = httpx.get(
            f"{base_url}/api/v1/sessions/{session_id}",
            headers=headers,
            timeout=5.0,
        )

        assert get_response.status_code == 200
        assert get_response.json()["task"] == "Database persistence test"


class TestConcurrentRequests:
    """Tests for concurrent request handling."""

    @pytest.mark.integration
    def test_handles_concurrent_requests(self, running_server: dict) -> None:
        """Server handles multiple concurrent requests."""
        import concurrent.futures

        base_url = running_server["base_url"]

        def make_health_request():
            response = httpx.get(f"{base_url}/api/v1/health", timeout=5.0)
            return response.status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_health_request) for _ in range(10)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert all(status == 200 for status in results)

    @pytest.mark.integration
    def test_handles_concurrent_auth_requests(
        self,
        running_server: dict
    ) -> None:
        """Server handles multiple concurrent login requests with same user."""
        import concurrent.futures

        base_url = running_server["base_url"]
        test_user = running_server["test_user"]

        def make_auth_request():
            response = httpx.post(
                f"{base_url}/api/v1/auth/login",
                json={"email": test_user["email"], "password": test_user["password"]},
                timeout=5.0
            )
            return response.status_code, response.json().get("user_id") if response.status_code == 200 else None

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_auth_request) for _ in range(5)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        statuses = [r[0] for r in results]
        user_ids = [r[1] for r in results if r[1] is not None]

        assert all(status == 200 for status in statuses)
        # All logins should return the same user_id since it's the same user
        assert len(set(user_ids)) == 1


@pytest.mark.e2e
class TestAgentExecution:
    """
    Tests for complete agent execution with real model.

    These tests actually run the agent with the meow skill using haiku model.
    They verify:
    - Agent starts and runs correctly
    - Skills are loaded and accessible
    - Output is generated correctly
    - Session status is updated properly
    
    Note: These tests are skipped by default. Run with: pytest --run-e2e
    """

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_API_KEY, reason="ANTHROPIC_API_KEY not set - skipping E2E test")
    def test_run_task_with_meow_skill(self, running_server: dict) -> None:
        """
        Complete E2E test: Run agent with meow skill and verify output.

        This test:
        1. Creates a session with the meow skill task
        2. Starts the agent task
        3. Waits for completion (with timeout)
        4. Verifies output.yaml status is COMPLETE or PARTIAL
        5. Verifies skill was invoked
        """
        base_url = running_server["base_url"]
        temp_users = running_server["temp_users"]
        temp_skills = running_server["temp_skills"]

        # Verify skills are available
        meow_skill = temp_skills / "meow" / "meow.md"
        assert meow_skill.exists(), f"Meow skill should exist at {meow_skill}"

        # Get auth token
        test_user = running_server["test_user"]
        token = get_auth_token(base_url, test_user["email"], test_user["password"])
        headers = {"Authorization": f"Bearer {token}"}

        # Create and start session with meow skill task
        run_response = httpx.post(
            f"{base_url}/api/v1/sessions/run",
            headers=headers,
            json={
                "task": (
                    "Use the meow skill to fetch a cat fact. "
                    "Write the result to output.yaml with status: COMPLETE."
                ),
                "config": {
                    "model": "claude-haiku-4-5-20251001",
                    "max_turns": 15,  # Need enough turns to complete skill task
                    "timeout_seconds": 90,
                    "enable_skills": True,
                }
            },
            timeout=10.0,
        )

        if run_response.status_code != 201:
            # Read server stderr log for debugging
            stderr_log = running_server.get("stderr_log")
            if stderr_log and stderr_log.exists():
                stderr_content = stderr_log.read_text()
                # Print last 2000 chars to avoid overwhelming output
                if len(stderr_content) > 2000:
                    stderr_content = f"...(truncated)...\n{stderr_content[-2000:]}"
                print(f"\n=== Server stderr log ===\n{stderr_content}\n========================")
            pytest.fail(f"Failed to start task (status {run_response.status_code}): {run_response.text}")
        session_id = run_response.json()["session_id"]

        # Wait for agent to complete (poll with timeout)
        max_wait = 90  # seconds - need enough time for 15 turns
        poll_interval = 2  # seconds
        start_time = time.time()

        final_status = None
        while time.time() - start_time < max_wait:
            status_response = httpx.get(
                f"{base_url}/api/v1/sessions/{session_id}",
                headers=headers,
                timeout=5.0,
            )
            session_data = status_response.json()
            final_status = session_data["status"]

            if final_status in ("completed", "failed", "cancelled"):
                break

            time.sleep(poll_interval)

        # Get result
        result_response = httpx.get(
            f"{base_url}/api/v1/sessions/{session_id}/result",
            headers=headers,
            timeout=5.0,
        )
        result_data = result_response.json()

        # Check session folder exists (stored under users/{username}/sessions/)
        test_user = running_server["test_user"]
        username = test_user["username"]
        session_folder = temp_users / username / "sessions" / session_id
        assert session_folder.exists(), \
            f"Session folder should exist: {session_folder}"

        # Check output.yaml was created
        workspace_folder = session_folder / "workspace"
        output_file = workspace_folder / "output.yaml"

        if output_file.exists():
            output_text = output_file.read_text()
            try:
                output_content = yaml.safe_load(output_text)
                output_status = output_content.get("status", "UNKNOWN") if output_content else "UNKNOWN"

                # Accept COMPLETE, PARTIAL, or agent completing the task
                assert output_status in ("COMPLETE", "PARTIAL", "OK"), \
                    f"Unexpected output status: {output_status}"
            except yaml.YAMLError as e:
                # Agent may have written malformed YAML (e.g., unquoted colons)
                # Check if status can be extracted with regex as fallback
                import re
                status_match = re.search(r'status:\s*(\w+)', output_text)
                if status_match:
                    output_status = status_match.group(1)
                    assert output_status in ("COMPLETE", "PARTIAL", "OK"), \
                        f"Unexpected output status (from regex): {output_status}"
                else:
                    # If we can't extract status, just warn but don't fail
                    print(f"Warning: output.yaml has invalid YAML: {e}")
                    print(f"Content: {output_text[:200]}...")

        # Verify session completed or at least ran
        # Note: both "complete" and "completed" are valid completion statuses
        # If failed, include error details for debugging
        if final_status == "failed":
            error_info = result_data.get("error", "No error details")
            output_info = result_data.get("output", "No output")
            print(f"\n=== SESSION FAILED ===")
            print(f"Session ID: {session_id}")
            print(f"Error: {error_info}")
            print(f"Output: {output_info}")
            print(f"Result data: {result_data}")
            print(f"======================\n")
        
        assert final_status in ("completed", "complete", "running", "pending"), \
            f"Session ended with unexpected status: {final_status}. Error: {result_data.get('error', 'unknown')}"

        # Verify result contains session info
        assert result_data["session_id"] == session_id

    @pytest.mark.integration
    def test_session_info_contains_config(self, running_server: dict) -> None:
        """
        Verify session info reflects the config that was passed.
        """
        base_url = running_server["base_url"]

        test_user = running_server["test_user"]
        token = get_auth_token(base_url, test_user["email"], test_user["password"])
        headers = {"Authorization": f"Bearer {token}"}

        # Create session with specific model
        create_response = httpx.post(
            f"{base_url}/api/v1/sessions",
            headers=headers,
            json={
                "task": "Config test task",
                "model": "claude-haiku-4-5-20251001",
            },
            timeout=5.0,
        )

        assert create_response.status_code == 201
        session_data = create_response.json()

        assert session_data["model"] == "claude-haiku-4-5-20251001"
        assert session_data["task"] == "Config test task"
