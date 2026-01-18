"""
Pytest configuration and fixtures for backend tests.

Provides fixtures for:
- In-memory test database
- FastAPI test client with mock dependencies
- Temporary sessions directory with automatic cleanup
- Mock services (agent runner)
"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# Add project root to path before importing project modules
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.database import Base, get_db  # noqa: E402
from src.api.main import create_app  # noqa: E402
from src.services.auth_service import AuthService  # noqa: E402
from src.services.agent_runner import AgentRunner  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "unit: marks tests as unit tests (fast, no external dependencies)"
    )
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (may use real database)"
    )
    config.addinivalue_line(
        "markers",
        "e2e: marks tests as end-to-end tests requiring real API calls (skipped by default)"
    )
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (skipped by default)"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    """
    Skip e2e and slow tests by default unless explicitly requested.
    
    Run e2e tests with: pytest -m e2e
    Run all tests including e2e: pytest --run-e2e
    """
    run_e2e = config.getoption("--run-e2e", default=False)
    
    if run_e2e:
        # Don't skip anything if --run-e2e is passed
        return
    
    skip_e2e = pytest.mark.skip(reason="E2E test skipped by default. Use --run-e2e to run.")
    
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip_e2e)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom command line options."""
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run end-to-end tests that require real API calls",
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Clean up after all tests complete to prevent hanging."""
    import os
    import threading
    import gc
    
    # Force garbage collection to clean up any pending resources
    gc.collect()
    
    # Give a small window for cleanup
    import time
    time.sleep(0.1)
    
    # Check for non-daemon threads that might be blocking (e.g., aiosqlite worker)
    main_thread = threading.main_thread()
    hanging_threads = []
    for thread in threading.enumerate():
        if thread is not main_thread and thread.is_alive() and not thread.daemon:
            # Try to join with a short timeout
            thread.join(timeout=0.5)
            if thread.is_alive():
                hanging_threads.append(thread.name)
    
    # If there are still hanging threads, force exit to prevent indefinite hang
    if hanging_threads:
        print(f"\nWARNING: Threads did not exit: {hanging_threads}. Forcing exit.")
        os._exit(exitstatus)


# In-memory SQLite for testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    # Cancel all pending tasks before closing
    pending = asyncio.all_tasks(loop)
    for task in pending:
        task.cancel()
    # Give tasks a chance to handle cancellation
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    loop.close()


@pytest.fixture(scope="function")
def temp_sessions_dir() -> Generator[Path, None, None]:
    """
    Create a temporary directory for test sessions.
    
    This directory is automatically cleaned up after each test,
    preventing session folder artifacts from accumulating.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="ag3ntum_test_sessions_"))
    yield temp_dir
    # Cleanup after test
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def test_engine():
    """Create a test database engine."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def test_session_factory(test_engine):
    """
    Create a shared async session factory for all test fixtures.

    This ensures test_user and test_app use the same database context.
    """
    return async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest_asyncio.fixture
async def test_session(test_session_factory) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async with test_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def test_db_override(test_session_factory):
    """
    Fixture that provides a dependency override for get_db.

    Returns a function that can be used with app.dependency_overrides.
    """
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            yield session

    return override_get_db


@pytest.fixture
def mock_agent_runner() -> MagicMock:
    """Create a mock agent runner that doesn't actually run agents."""
    runner = MagicMock(spec=AgentRunner)
    runner.is_running.return_value = False
    runner.start_task = AsyncMock()
    runner.cancel_task = AsyncMock(return_value=True)
    runner.get_result.return_value = None
    return runner


@pytest.fixture
def auth_service() -> AuthService:
    """Create an auth service instance for testing."""
    return AuthService()


@pytest.fixture
def test_session_service(temp_sessions_dir):
    """
    Create a session service that uses temp directory.
    
    This prevents test sessions from being created in the real sessions folder.
    """
    from src.services.session_service import SessionService
    return SessionService(sessions_dir=temp_sessions_dir)


@pytest.fixture
def test_app(test_db_override, mock_agent_runner, temp_sessions_dir):
    """
    Create a FastAPI app configured for testing.

    Uses in-memory database, mock agent runner, and temp sessions directory.
    """
    from src.services.session_service import SessionService

    # Create session service with temp directory BEFORE patching
    temp_session_service = SessionService(sessions_dir=temp_sessions_dir)

    # Patch at multiple levels to ensure temp directory is used
    with patch("src.api.main.load_api_config") as mock_config:
        mock_config.return_value = {
            "api": {
                "host": "0.0.0.0",
                "port": 40080,
                "cors_origins": ["http://localhost:50080"],
            }
        }

        # Patch session_service at the routes level (this is where it's imported)
        with patch("src.api.routes.sessions.session_service", temp_session_service):
            # Patch USERS_DIR to use temp directory for user session folders
            with patch("src.api.routes.sessions.USERS_DIR", temp_sessions_dir):
                # Patch validate_user_environment to skip filesystem checks in tests
                with patch("src.services.auth_service.AuthService.validate_user_environment"):
                    app = create_app()

                    # Override database dependency
                    app.dependency_overrides[get_db] = test_db_override

                    # Patch agent runner for session routes
                    with patch("src.api.routes.sessions.agent_runner", mock_agent_runner):
                        yield app

                    app.dependency_overrides.clear()


@pytest.fixture
def client(test_app) -> Generator[TestClient, None, None]:
    """Create a synchronous test client."""
    with TestClient(test_app) as c:
        yield c


@pytest_asyncio.fixture
async def async_client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test"
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def test_user(test_session_factory) -> dict:
    """Create a test user and return credentials."""
    import secrets
    import uuid

    try:
        import bcrypt
        has_bcrypt = True
    except ImportError:
        has_bcrypt = False

    # Generate unique email for each test
    test_id = str(uuid.uuid4())[:8]
    username = f"testuser_{test_id}"
    email = f"test_{test_id}@example.com"
    password = "test123"

    # Hash password
    if has_bcrypt:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    else:
        # Fallback for tests without bcrypt - use a simple hash
        import hashlib
        password_hash = hashlib.sha256(password.encode()).hexdigest()

    # Generate per-user JWT secret
    jwt_secret = secrets.token_urlsafe(32)

    # Create user directly in database using the shared session factory
    from src.db.models import User
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        email=email,
        password_hash=password_hash,
        role="user",
        jwt_secret=jwt_secret,
        linux_uid=None,
        is_active=True,
    )

    async with test_session_factory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)

        return {
            "id": user.id,
            "username": username,
            "email": email,
            "password": password,
            "jwt_secret": user.jwt_secret,
        }


@pytest.fixture
def auth_headers(client, test_user) -> dict:
    """Get authentication headers with a valid token."""
    response = client.post(
        "/api/v1/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    if response.status_code != 200:
        raise RuntimeError(f"Auth failed: {response.status_code} - {response.text}")
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def async_auth_headers(async_client, test_user) -> dict:
    """Get authentication headers for async client."""
    response = await async_client.post(
        "/api/v1/auth/login",
        json={"email": test_user["email"], "password": test_user["password"]},
    )
    if response.status_code != 200:
        raise RuntimeError(f"Auth failed: {response.status_code} - {await response.text()}")
    data = response.json()
    token = data["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def created_session(client, auth_headers) -> dict:
    """Create a session and return its data for use in tests."""
    response = client.post(
        "/api/v1/sessions",
        headers=auth_headers,
        json={"task": "Fixture test task"}
    )
    assert response.status_code == 201
    return response.json()


@pytest_asyncio.fixture
async def second_test_user(test_session_factory) -> dict:
    """
    Create a second test user for isolation tests.

    Returns credentials dict similar to test_user.
    """
    import secrets
    import uuid

    try:
        import bcrypt
        has_bcrypt = True
    except ImportError:
        has_bcrypt = False

    # Generate unique email for each test
    test_id = str(uuid.uuid4())[:8]
    username = f"testuser2_{test_id}"
    email = f"test2_{test_id}@example.com"
    password = "test456"

    # Hash password
    if has_bcrypt:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    else:
        import hashlib
        password_hash = hashlib.sha256(password.encode()).hexdigest()

    # Generate per-user JWT secret
    jwt_secret = secrets.token_urlsafe(32)

    # Create user directly in database using the shared session factory
    from src.db.models import User
    user = User(
        id=str(uuid.uuid4()),
        username=username,
        email=email,
        password_hash=password_hash,
        role="user",
        jwt_secret=jwt_secret,
        linux_uid=None,
        is_active=True,
    )

    async with test_session_factory() as session:
        session.add(user)
        await session.commit()
        await session.refresh(user)

        return {
            "id": user.id,
            "username": username,
            "email": email,
            "password": password,
            "jwt_secret": user.jwt_secret,
        }


@pytest.fixture
def second_auth_headers(client, second_test_user) -> dict:
    """Get authentication headers for the second test user."""
    response = client.post(
        "/api/v1/auth/login",
        json={"email": second_test_user["email"], "password": second_test_user["password"]},
    )
    if response.status_code != 200:
        raise RuntimeError(f"Auth failed: {response.status_code} - {response.text}")
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# Cleanup fixture to run after all tests in the module
@pytest.fixture(scope="session", autouse=True)
def cleanup_test_artifacts():
    """
    Clean up any leftover test artifacts after all tests complete.
    
    This is a safety net in case individual cleanups fail.
    """
    yield
    # After all tests, clean up any temp directories that might remain
    temp_base = Path(tempfile.gettempdir())
    for item in temp_base.iterdir():
        if item.is_dir() and item.name.startswith("ag3ntum_test_sessions_"):
            try:
                shutil.rmtree(item, ignore_errors=True)
            except Exception:
                pass
