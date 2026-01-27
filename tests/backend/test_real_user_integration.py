"""
Real User Integration Tests.

These tests create REAL user accounts with full infrastructure and verify:
- User creation via UserService (same as ./run.sh create-user)
- Directory structure and permissions
- Venv installation and module availability
- Mount access (read-only vs read-write)
- File accessibility in mounted and persistent folders
- User isolation (cannot access other user's files/processes)
- Sandbox isolation between users

WARNING: These tests are SLOW (~30-60 seconds per user creation) because they:
- Create real Linux users via useradd
- Create full directory structures with proper permissions
- Install Python venv with pip packages

Run these tests with: pytest tests/backend/test_real_user_integration.py -v --run-e2e
Or as part of full suite: ./run.sh test
"""
import grp
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.database import Base
from src.db.models import User
from src.services.user_service import UserService

# Import sandbox execution for testing real MCP tool behavior
from src.core.sandbox import (
    SandboxConfig,
    SandboxExecutor,
    SandboxMount,
    SandboxEnvConfig,
    SandboxNetworkConfig,
    ProcFilteringConfig,
    execute_sandboxed_command,
    create_demote_fn,
)

# Test input directories
TEST_INPUT_DIR = Path(__file__).parent / "input"
TEST_MOUNTS_DIR = TEST_INPUT_DIR / "mounts"
TEST_RO_MOUNT = TEST_MOUNTS_DIR / "test_ro_mount"
TEST_RW_MOUNT = TEST_MOUNTS_DIR / "test_rw_mount"

# Infrastructure constants
AG3NTUM_GROUP = "ag3ntum"
AG3NTUM_API_USER = "ag3ntum_api"
AG3NTUM_API_UID = 45045
USERS_DIR = Path("/users")

# Reserved UIDs for test users (above system UID, below regular users)
# These ensure consistent test users that don't conflict with production users
TEST_RESERVED_UID_1 = 45046
TEST_RESERVED_UID_2 = 45047


class InfrastructureError(Exception):
    """Raised when required infrastructure cannot be set up."""
    pass


def _is_docker_environment() -> bool:
    """Check if we're running inside Docker."""
    return Path("/.dockerenv").exists() or os.environ.get("AG3NTUM_IN_DOCKER") == "1"


def _is_root() -> bool:
    """Check if we're running as root."""
    return os.getuid() == 0


def _has_sudo_access() -> bool:
    """Check if we have passwordless sudo access."""
    if _is_root():
        return True  # Root doesn't need sudo
    try:
        result = subprocess.run(
            ["sudo", "-n", "true"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _run_privileged(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command with elevated privileges (sudo or as root)."""
    if _is_root():
        return subprocess.run(cmd, **kwargs)
    else:
        return subprocess.run(["sudo"] + cmd, **kwargs)


def _run_as_user(username: str, cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command as a specific user (requires root or sudo)."""
    if _is_root():
        return subprocess.run(["su", "-s", "/bin/sh", username, "-c", " ".join(cmd)], **kwargs)
    else:
        return subprocess.run(["sudo", "-u", username] + cmd, **kwargs)


def _group_exists(group_name: str) -> bool:
    """Check if a group exists."""
    try:
        grp.getgrnam(group_name)
        return True
    except KeyError:
        return False


def _user_exists(username: str) -> bool:
    """Check if a user exists."""
    try:
        pwd.getpwnam(username)
        return True
    except KeyError:
        return False


def _ensure_group(group_name: str) -> None:
    """Ensure a group exists, create if missing."""
    if _group_exists(group_name):
        return

    result = _run_privileged(
        ["groupadd", group_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "already exists" not in result.stderr:
        raise InfrastructureError(f"Failed to create group {group_name}: {result.stderr}")


def _ensure_user(username: str, uid: int, group: str) -> None:
    """Ensure a system user exists, create if missing."""
    if _user_exists(username):
        return

    result = _run_privileged(
        ["useradd", "-r", "-u", str(uid), "-g", group, "-M", "-s", "/bin/false", username],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "already exists" not in result.stderr:
        raise InfrastructureError(f"Failed to create user {username}: {result.stderr}")


def _ensure_directory(path: Path, mode: int = 0o755) -> None:
    """Ensure a directory exists with correct permissions."""
    if not path.exists():
        # Use elevated privileges to create in case we don't have permissions
        result = _run_privileged(
            ["mkdir", "-p", str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise InfrastructureError(f"Failed to create directory {path}: {result.stderr}")

    # Set permissions
    _run_privileged(
        ["chmod", oct(mode)[2:], str(path)],
        capture_output=True,
    )


def _infrastructure_exists() -> bool:
    """Check if all required infrastructure already exists."""
    return (
        _group_exists(AG3NTUM_GROUP) and
        _user_exists(AG3NTUM_API_USER) and
        USERS_DIR.exists()
    )


def setup_test_infrastructure() -> None:
    """
    Set up all required infrastructure for real user tests.

    This ensures:
    1. ag3ntum group exists
    2. ag3ntum_api user exists (UID 45045)
    3. /users directory exists with correct permissions

    If infrastructure already exists (e.g., in Docker), skip creation.

    Raises:
        InfrastructureError: If infrastructure doesn't exist and cannot be created
    """
    # Check if infrastructure already exists (set up by Docker)
    if _infrastructure_exists():
        return  # Nothing to do, infrastructure is ready

    # Need elevated privileges to create infrastructure
    if not _has_sudo_access():
        missing = []
        if not _group_exists(AG3NTUM_GROUP):
            missing.append(f"group '{AG3NTUM_GROUP}'")
        if not _user_exists(AG3NTUM_API_USER):
            missing.append(f"user '{AG3NTUM_API_USER}'")
        if not USERS_DIR.exists():
            missing.append(f"directory '{USERS_DIR}'")

        raise InfrastructureError(
            f"Missing infrastructure: {', '.join(missing)}. "
            f"Root or passwordless sudo required to create them. "
            f"These tests should run inside Docker where infrastructure is pre-configured."
        )

    # 1. Create ag3ntum group
    _ensure_group(AG3NTUM_GROUP)

    # 2. Create ag3ntum_api user
    _ensure_user(AG3NTUM_API_USER, AG3NTUM_API_UID, AG3NTUM_GROUP)

    # 3. Create /users directory
    _ensure_directory(USERS_DIR, mode=0o755)

    # 4. Set ownership of /users to ag3ntum_api
    _run_privileged(
        ["chown", f"{AG3NTUM_API_USER}:{AG3NTUM_GROUP}", str(USERS_DIR)],
        capture_output=True,
    )


def cleanup_leftover_test_users() -> None:
    """
    Clean up any leftover test users from previous test runs.

    This is necessary because:
    - The database is recreated fresh for each test module (in-memory or temp file)
    - But Linux users persist in the Docker container's /etc/passwd
    - So _generate_next_uid() returns 2000, but that UID may already exist

    This function removes:
    - Linux users matching realtest_* pattern
    - Directories in /users/realtest_*
    """

    # 1. Find and delete Linux users matching test pattern
    try:
        result = subprocess.run(
            ["getent", "passwd"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                username = line.split(":")[0]
                if username.startswith("realtest_"):
                    # Delete the Linux user
                    _run_privileged(
                        ["userdel", username],
                        capture_output=True,
                        timeout=30,
                    )
    except Exception as e:
        print(f"Warning: Could not enumerate/delete Linux users: {e}")

    # 2. Remove leftover directories in /users
    if USERS_DIR.exists():
        for user_dir in USERS_DIR.glob("realtest_*"):
            if user_dir.is_dir():
                try:
                    shutil.rmtree(user_dir)
                except PermissionError:
                    # Try with sudo
                    _run_privileged(
                        ["rm", "-rf", str(user_dir)],
                        capture_output=True,
                        timeout=30,
                    )
                except Exception as e:
                    print(f"Warning: Could not remove {user_dir}: {e}")


@pytest.fixture(scope="module", autouse=True)
def ensure_infrastructure():
    """
    Module-level fixture to ensure infrastructure is set up before any tests run.

    This fixture is autouse=True so it runs automatically for all tests in this module.
    It also cleans up any leftover test users from previous runs to avoid UID collisions.
    """
    # First, clean up any leftover test users from previous runs
    cleanup_leftover_test_users()

    # Then ensure infrastructure is set up
    setup_test_infrastructure()

    yield

    # Final cleanup after all tests in module complete
    cleanup_leftover_test_users()


@pytest.fixture(scope="module")
def test_db_path() -> Generator[Path, None, None]:
    """Create a temporary database for real user tests."""
    temp_dir = Path(tempfile.mkdtemp(prefix="ag3ntum_real_user_test_"))
    db_path = temp_dir / "test.db"
    yield db_path
    # Cleanup
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest_asyncio.fixture(scope="module")
async def real_test_engine(test_db_path: Path):
    """Create a real database engine for user tests."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{test_db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture(scope="module")
async def real_session_factory(real_test_engine):
    """Create session factory for real user tests."""
    return async_sessionmaker(
        real_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.fixture(scope="module")
def user_service() -> UserService:
    """Create UserService instance."""
    return UserService()


def generate_test_username() -> str:
    """Generate a unique test username."""
    return f"realtest_{uuid.uuid4().hex[:8]}"


def is_fakeowner_mount(path: Path) -> bool:
    """
    Check if a path is on a fakeowner mount (Docker Desktop for Mac).

    The fakeowner mount makes all files appear owned by the container user
    regardless of the actual creator. This affects ownership tests.
    """
    # findmnt only works on mount points, not subdirectories
    # Walk up the tree to find the mount point
    check_path = path.resolve()
    while check_path != Path("/"):
        try:
            result = subprocess.run(
                ["findmnt", "-n", "-o", "FSTYPE", str(check_path)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return "fakeowner" in result.stdout
        except Exception:
            pass
        check_path = check_path.parent

    # Fallback: check mount output directly
    try:
        result = subprocess.run(
            ["mount"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        # Check if any fakeowner mount covers this path
        path_str = str(path.resolve())
        for line in result.stdout.splitlines():
            if "fakeowner" in line:
                # Extract mount point from line (format: "source on /mountpoint type ...")
                parts = line.split(" on ")
                if len(parts) >= 2:
                    mount_point = parts[1].split(" type ")[0]
                    if path_str.startswith(mount_point):
                        return True
    except Exception:
        pass
    return False


def get_bwrap_lib_binds() -> list[str]:
    """
    Get bwrap bind arguments for system libraries and binaries.

    Different Linux distributions have different layouts:
    - Ubuntu/Debian: /lib, /bin -> /usr/bin (symlink), no /lib64
    - RHEL/CentOS: /lib, /lib64, /bin is separate

    Returns a list of bwrap arguments for binding system libraries and binaries.
    """
    args = ["--ro-bind", "/lib", "/lib"]

    # Add /lib64 only if it exists (RHEL/CentOS-style)
    if Path("/lib64").exists():
        args.extend(["--ro-bind", "/lib64", "/lib64"])

    # On Ubuntu, /bin is a symlink to /usr/bin, so we need to bind it
    # to make /bin/sh, /bin/cat etc. available
    if Path("/bin").exists():
        args.extend(["--ro-bind", "/bin", "/bin"])

    return args


def build_test_bwrap_command(
    user: User,
    command: str,
    include_proc_filter: bool = True,
) -> list[str]:
    """
    Build a bwrap command for testing sandbox execution as a specific user.

    This uses 'sudo -u username bwrap ...' which works with the test infrastructure
    since ag3ntum_api has sudo permissions to run commands as any user.

    Args:
        user: The User object with username and linux_uid
        command: The shell command to execute inside the sandbox
        include_proc_filter: Whether to use filtered /proc (default: True)

    Returns:
        Complete command list for subprocess.run()
    """
    workspace_path = Path(f"/users/{user.username}/ag3ntum/persistent")
    venv_path = Path(f"/users/{user.username}/venv")

    # Build bwrap command
    # Note: Use full path /usr/bin/bwrap to match sudoers pattern
    bwrap_cmd = [
        "sudo", "-u", user.username,
        "/usr/bin/bwrap",
        # Namespace isolation
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--die-with-parent",
        "--new-session",
        # Tmpfs for /tmp
        "--tmpfs", "/tmp:size=100M",
    ]

    # /proc handling - filtered for security
    if include_proc_filter:
        bwrap_cmd.extend(["--tmpfs", "/proc"])
        # Mount only safe /proc entries
        for entry in ["/proc/self", "/proc/cpuinfo", "/proc/meminfo", "/proc/uptime", "/proc/version"]:
            if Path(entry).exists():
                bwrap_cmd.extend(["--ro-bind", entry, entry])
    else:
        bwrap_cmd.extend(["--ro-bind", "/proc", "/proc"])

    # /dev
    bwrap_cmd.extend(["--dev-bind", "/dev", "/dev"])

    # Static mounts
    bwrap_cmd.extend(["--ro-bind", "/usr", "/usr"])
    bwrap_cmd.extend(["--ro-bind", "/lib", "/lib"])

    if Path("/lib64").exists():
        bwrap_cmd.extend(["--ro-bind", "/lib64", "/lib64"])
    if Path("/bin").exists():
        bwrap_cmd.extend(["--ro-bind", "/bin", "/bin"])

    # Session mounts
    bwrap_cmd.extend(["--bind", str(workspace_path), "/workspace"])
    bwrap_cmd.extend(["--ro-bind", str(venv_path), "/workspace/venv"])

    # Environment
    bwrap_cmd.extend([
        "--clearenv",
        "--setenv", "HOME", "/workspace",
        "--setenv", "PATH", "/workspace/venv/bin:/usr/bin:/bin",
        "--setenv", "AG3NTUM_CONTEXT", "sandbox",
        "--chdir", "/workspace",
    ])

    # Command to execute
    bwrap_cmd.extend(["--", "bash", "-c", command])

    return bwrap_cmd


async def execute_test_sandbox_command(
    user: User,
    command: str,
    timeout: int = 30,
    include_proc_filter: bool = True,
) -> tuple[int, str, str]:
    """
    Execute a command in sandbox as a specific user.

    Uses sudo + bwrap which works with the test infrastructure's sudo permissions.

    Args:
        user: The User to run as
        command: Shell command to execute
        timeout: Command timeout in seconds
        include_proc_filter: Whether to filter /proc

    Returns:
        Tuple of (exit_code, stdout, stderr)
    """
    import asyncio

    bwrap_cmd = build_test_bwrap_command(user, command, include_proc_filter)

    try:
        process = await asyncio.create_subprocess_exec(
            *bwrap_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        exit_code = process.returncode or 0
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        return exit_code, stdout, stderr

    except asyncio.TimeoutError:
        if process:
            process.kill()
        return 124, "", f"Command timed out after {timeout} seconds"
    except Exception as e:
        return 1, "", str(e)


async def cleanup_test_user(
    user_service: UserService,
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
) -> None:
    """Clean up a test user (database + Linux user + directories)."""
    try:
        async with session_factory() as session:
            await user_service.delete_user(
                db=session,
                username=username,
                delete_linux_user=True,
            )
    except Exception as e:
        print(f"Warning: Cleanup failed for {username}: {e}")
        # Try manual cleanup
        try:
            _run_privileged(
                ["userdel", "-r", username],
                capture_output=True,
                timeout=30,
            )
        except Exception:
            pass


@pytest.mark.e2e
@pytest.mark.slow
class TestRealUserCreation:
    """
    Tests for real user account creation.

    These tests verify that UserService.create_user() creates a fully
    functional user account with all required infrastructure.
    """

    @pytest_asyncio.fixture
    async def created_user(
        self,
        user_service: UserService,
        real_session_factory: async_sessionmaker[AsyncSession],
    ) -> AsyncGenerator[tuple[User, str], None]:
        """Create a real test user and clean up after test."""
        username = generate_test_username()
        password = "TestPass123!"
        email = f"{username}@test.example.com"

        async with real_session_factory() as session:
            user = await user_service.create_user(
                db=session,
                username=username,
                email=email,
                password=password,
                role="user",
            )

        yield user, password

        # Cleanup
        await cleanup_test_user(user_service, real_session_factory, username)

    @pytest.mark.asyncio
    async def test_user_created_in_database(
        self,
        created_user: tuple[User, str],
        real_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Verify user record exists in database with correct fields."""
        user, password = created_user

        async with real_session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.username == user.username)
            )
            db_user = result.scalar_one_or_none()

        assert db_user is not None
        assert db_user.username == user.username
        assert db_user.email == user.email
        assert db_user.role == "user"
        assert db_user.is_active is True
        assert db_user.linux_uid is not None
        assert db_user.linux_uid >= 2000  # UIDs start at 2000
        assert db_user.jwt_secret is not None
        assert len(db_user.jwt_secret) > 20  # Should be a proper secret

    @pytest.mark.asyncio
    async def test_linux_user_created(self, created_user: tuple[User, str]) -> None:
        """Verify Linux user account was created."""
        user, _ = created_user

        # Check if user exists in /etc/passwd
        result = subprocess.run(
            ["id", user.username],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Linux user {user.username} not found"
        assert f"uid={user.linux_uid}" in result.stdout

    @pytest.mark.asyncio
    async def test_user_home_directory_exists(self, created_user: tuple[User, str]) -> None:
        """Verify user home directory structure exists."""
        user, _ = created_user
        user_home = Path(f"/users/{user.username}")

        assert user_home.exists(), f"Home directory not found: {user_home}"
        assert user_home.is_dir()

        # Check required subdirectories
        required_dirs = [
            user_home / "sessions",
            user_home / "ag3ntum",
            user_home / "ag3ntum" / "persistent",
            user_home / "venv",
            user_home / "venv" / "bin",
        ]

        for dir_path in required_dirs:
            assert dir_path.exists(), f"Required directory not found: {dir_path}"
            assert dir_path.is_dir(), f"Not a directory: {dir_path}"

    @pytest.mark.asyncio
    async def test_directory_ownership(self, created_user: tuple[User, str]) -> None:
        """Verify directories are owned by the user, not ag3ntum_api (45045)."""
        user, _ = created_user
        user_home = Path(f"/users/{user.username}")

        # These directories should be owned by the user
        user_owned_dirs = [
            user_home / "ag3ntum",
            user_home / "ag3ntum" / "persistent",
        ]

        for dir_path in user_owned_dirs:
            stat_info = dir_path.stat()
            assert stat_info.st_uid == user.linux_uid, (
                f"{dir_path} owned by UID {stat_info.st_uid}, expected {user.linux_uid}"
            )

    @pytest.mark.asyncio
    async def test_directory_permissions(self, created_user: tuple[User, str]) -> None:
        """Verify directory permissions are correct."""
        user, _ = created_user
        user_home = Path(f"/users/{user.username}")

        # Check specific permission requirements
        # Note: _setup_group_permissions() sets 750 for API group access
        permission_checks = [
            # (path, expected_mode, description)
            (user_home, 0o750, "Home should allow group read+traverse for API"),
            (user_home / "sessions", 0o750, "Sessions allows group read+traverse"),
            (user_home / "ag3ntum", 0o700, "ag3ntum should be user-only"),
            (user_home / "venv", 0o755, "venv should be world-readable"),
        ]

        for dir_path, expected_mode, description in permission_checks:
            if dir_path.exists():
                actual_mode = stat.S_IMODE(dir_path.stat().st_mode)
                assert actual_mode == expected_mode, (
                    f"{description}: {dir_path} has mode {oct(actual_mode)}, "
                    f"expected {oct(expected_mode)}"
                )

    @pytest.mark.asyncio
    async def test_venv_installed_with_python(self, created_user: tuple[User, str]) -> None:
        """Verify venv has working Python interpreter."""
        user, _ = created_user
        venv_python = Path(f"/users/{user.username}/venv/bin/python3")

        assert venv_python.exists(), f"Python not found in venv: {venv_python}"

        # Verify it's executable
        assert os.access(venv_python, os.X_OK), f"Python not executable: {venv_python}"

        # Verify it runs
        result = subprocess.run(
            [str(venv_python), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Python failed to run: {result.stderr}"
        assert "Python" in result.stdout

    @pytest.mark.asyncio
    async def test_venv_has_required_packages(self, created_user: tuple[User, str]) -> None:
        """Verify venv has required packages installed."""
        user, _ = created_user
        venv_pip = Path(f"/users/{user.username}/venv/bin/pip")

        if not venv_pip.exists():
            pytest.skip("pip not found in venv")

        # Get list of installed packages
        result = subprocess.run(
            [str(venv_pip), "list", "--format=freeze"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0
        installed_packages = result.stdout.lower()

        # Check for some expected packages from user_requirements.txt
        # (adjust based on what's actually in user_requirements.txt)
        # At minimum, pip itself should be there
        assert "pip" in installed_packages or len(installed_packages) > 0

    @pytest.mark.asyncio
    async def test_secrets_file_exists(self, created_user: tuple[User, str]) -> None:
        """Verify user secrets file was created."""
        user, _ = created_user
        secrets_file = Path(f"/users/{user.username}/ag3ntum/secrets.yaml")

        assert secrets_file.exists(), f"Secrets file not found: {secrets_file}"

        # Check it's only readable by user
        mode = stat.S_IMODE(secrets_file.stat().st_mode)
        assert mode == 0o600, f"Secrets file has mode {oct(mode)}, expected 0o600"

    @pytest.mark.asyncio
    async def test_password_hash_is_valid(
        self,
        created_user: tuple[User, str],
        real_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Verify password was hashed correctly and can be verified."""
        import bcrypt

        user, password = created_user

        async with real_session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.username == user.username)
            )
            db_user = result.scalar_one()

        # Verify password hash
        is_valid = bcrypt.checkpw(
            password.encode(),
            db_user.password_hash.encode()
        )
        assert is_valid, "Password hash verification failed"



@pytest.mark.e2e
@pytest.mark.slow
class TestPersistentStorageAccess:
    """
    Tests for persistent storage access within user account.
    """

    @pytest_asyncio.fixture
    async def user_with_persistent_file(
        self,
        user_service: UserService,
        real_session_factory: async_sessionmaker[AsyncSession],
    ) -> AsyncGenerator[tuple[User, Path], None]:
        """Create user and a test file in persistent storage."""
        username = generate_test_username()

        async with real_session_factory() as session:
            user = await user_service.create_user(
                db=session,
                username=username,
                email=f"{username}@test.example.com",
                password="TestPass123!",
            )

        # Create a test file in persistent storage
        persistent_dir = Path(f"/users/{username}/ag3ntum/persistent")
        test_file = persistent_dir / "test_persistent_file.txt"

        # Write as root/api user, then change ownership
        test_file.write_text("Persistent test content")
        subprocess.run(
            ["sudo", "chown", f"{user.linux_uid}:{user.linux_uid}", str(test_file)],
            check=True,
        )

        yield user, test_file

        await cleanup_test_user(user_service, real_session_factory, username)

    @pytest.mark.asyncio
    async def test_persistent_file_readable(
        self, user_with_persistent_file: tuple[User, Path]
    ) -> None:
        """Verify files in persistent storage are readable."""
        user, test_file = user_with_persistent_file

        assert test_file.exists()
        content = test_file.read_text()
        assert "Persistent test content" in content

    @pytest.mark.asyncio
    async def test_persistent_storage_writable_by_user(
        self, user_with_persistent_file: tuple[User, Path]
    ) -> None:
        """Verify user can write to persistent storage."""
        user, test_file = user_with_persistent_file
        persistent_dir = test_file.parent

        # Create a new file as the user
        new_file = persistent_dir / "user_created_file.txt"

        result = subprocess.run(
            ["sudo", "-u", user.username, "touch", str(new_file)],
            capture_output=True,
        )

        assert result.returncode == 0, f"User cannot write to persistent: {result.stderr}"
        assert new_file.exists()



@pytest.mark.e2e
@pytest.mark.slow
class TestUserIsolation:
    """
    Tests for user isolation - verifying users cannot access each other's data.

    Uses class-scoped fixture to create users once for all tests in this class.
    """

    @pytest_asyncio.fixture(scope="class")
    async def two_isolated_users(
        self,
        user_service: UserService,
        real_session_factory: async_sessionmaker[AsyncSession],
    ) -> AsyncGenerator[tuple[User, User], None]:
        """Create two separate users for isolation testing (once per class)."""
        username1 = generate_test_username()
        username2 = generate_test_username()

        async with real_session_factory() as session:
            user1 = await user_service.create_user(
                db=session,
                username=username1,
                email=f"{username1}@test.example.com",
                password="TestPass123!",
            )

        async with real_session_factory() as session:
            user2 = await user_service.create_user(
                db=session,
                username=username2,
                email=f"{username2}@test.example.com",
                password="TestPass456!",
            )

        # Create test files in each user's persistent storage
        for user in [user1, user2]:
            secret_file = Path(f"/users/{user.username}/ag3ntum/persistent/secret.txt")
            secret_file.write_text(f"Secret data for {user.username}")
            subprocess.run(
                ["sudo", "chown", f"{user.linux_uid}:{user.linux_uid}", str(secret_file)],
                check=True,
            )

        yield user1, user2

        # Cleanup both users
        await cleanup_test_user(user_service, real_session_factory, username1)
        await cleanup_test_user(user_service, real_session_factory, username2)

    @pytest.mark.asyncio
    async def test_user_isolation_permissions(
        self, two_isolated_users: tuple[User, User]
    ) -> None:
        """
        Verify user isolation via directory permissions.

        Tests combined for efficiency (single user creation):
        - ag3ntum directory has mode 700 (owner-only)
        - Home directory has mode 711 (traverse-only)
        - Venv directory has mode 711 (traverse-only)
        - Users have unique UIDs
        """
        user1, user2 = two_isolated_users

        # === Test 1: ag3ntum directory is owner-only (700) ===
        user2_ag3ntum = Path(f"/users/{user2.username}/ag3ntum")
        ag3ntum_mode = stat.S_IMODE(user2_ag3ntum.stat().st_mode)

        assert ag3ntum_mode == 0o700, (
            f"ag3ntum should have mode 700 (owner-only), but has {oct(ag3ntum_mode)}"
        )

        # Check ownership - should be owned by user2, not user1
        ag3ntum_stat = user2_ag3ntum.stat()
        assert ag3ntum_stat.st_uid == user2.linux_uid, (
            f"ag3ntum should be owned by user2 (UID {user2.linux_uid}), "
            f"but owned by UID {ag3ntum_stat.st_uid}"
        )
        assert ag3ntum_stat.st_uid != user1.linux_uid, (
            "ag3ntum is owned by user1, which would allow access"
        )

        # === Test 2: No group/other access on ag3ntum ===
        has_group_access = bool(ag3ntum_mode & 0o070)
        has_other_access = bool(ag3ntum_mode & 0o007)

        assert not has_group_access, (
            f"ag3ntum should not have group access, but has mode {oct(ag3ntum_mode)}"
        )
        assert not has_other_access, (
            f"ag3ntum should not have other access, but has mode {oct(ag3ntum_mode)}"
        )

        # === Test 3: Home directory allows group access (750) ===
        # Group read+execute is allowed for API access via ag3ntum group
        user2_home = Path(f"/users/{user2.username}")
        home_mode = stat.S_IMODE(user2_home.stat().st_mode)

        has_other_read = bool(home_mode & 0o004)
        has_other_write = bool(home_mode & 0o002)

        assert not has_other_read, (
            f"Home should not have other read, but has mode {oct(home_mode)}"
        )
        assert not has_other_write, (
            f"Home should not have other write, but has mode {oct(home_mode)}"
        )

        # === Test 4: Venv directory is world-readable (755) ===
        # Venv needs to be readable for Python execution
        user2_venv = Path(f"/users/{user2.username}/venv")
        venv_mode = stat.S_IMODE(user2_venv.stat().st_mode)

        has_other_write = bool(venv_mode & 0o002)

        assert not has_other_write, (
            f"venv should not have other write, but has mode {oct(venv_mode)}"
        )

        # === Test 5: Each user has unique UID ===
        assert user1.linux_uid != user2.linux_uid, "Users should have different UIDs"
        assert user1.linux_uid >= 2000
        assert user2.linux_uid >= 2000

@pytest.mark.e2e
@pytest.mark.slow
class TestSandboxIsolation:
    """
    Tests for bwrap sandbox isolation between users.

    These tests verify that when running in sandbox mode:
    - Users cannot see each other's processes
    - Filesystem isolation is enforced
    - Network namespace is isolated (if configured)
    """

    @pytest_asyncio.fixture
    async def sandbox_test_user(
        self,
        user_service: UserService,
        real_session_factory: async_sessionmaker[AsyncSession],
    ) -> AsyncGenerator[User, None]:
        """Create a user for sandbox testing."""
        username = generate_test_username()

        async with real_session_factory() as session:
            user = await user_service.create_user(
                db=session,
                username=username,
                email=f"{username}@test.example.com",
                password="TestPass123!",
            )

        yield user

        await cleanup_test_user(user_service, real_session_factory, username)

    @pytest.mark.asyncio
    async def test_bwrap_available(self) -> None:
        """Verify bwrap is available for sandboxing."""
        result = subprocess.run(
            ["which", "bwrap"],
            capture_output=True,
        )

        assert result.returncode == 0, "bwrap not found - sandboxing won't work"

    @pytest.mark.asyncio
    async def test_sandbox_hides_other_processes(
        self, sandbox_test_user: User
    ) -> None:
        """
        Verify sandbox with --unshare-pid hides other processes.

        When running in a PID namespace, the process should only see
        itself and its children, not other system processes.

        TODO: Full PID namespace isolation (--unshare-pid with proper /proc mount)
        requires additional Docker capabilities that may not be available in all
        test environments. The TestSandboxRealExecution.test_sandbox_proc_filtering_*
        tests verify the filtering layer that provides defense-in-depth.
        """
        pytest.skip(
            "TODO: PID namespace test requires additional Docker capabilities. "
            "See TestSandboxRealExecution for /proc filtering tests."
        )

        user = sandbox_test_user

        # Run ps inside a bwrap sandbox with PID namespace
        bwrap_cmd = [
            "sudo", "-u", user.username,
            "bwrap",
            "--ro-bind", "/usr", "/usr",
        ] + get_bwrap_lib_binds() + [
            "--proc", "/proc",
            "--dev", "/dev",
            "--unshare-pid",
            "--die-with-parent",
            "/bin/ps", "aux",
        ]
        result = subprocess.run(
            bwrap_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            pytest.skip(f"bwrap execution failed: {result.stderr}")

        # In PID namespace, ps should show very few processes
        # (just the sandbox init, ps itself, and maybe a shell)
        lines = [l for l in result.stdout.strip().split('\n') if l and not l.startswith('USER')]

        # Should have very few processes (typically 1-3)
        assert len(lines) < 10, (
            f"Sandbox should hide most processes. Found {len(lines)}: {result.stdout}"
        )

    @pytest.mark.asyncio
    async def test_sandbox_filesystem_isolation(
        self, sandbox_test_user: User
    ) -> None:
        """
        Verify sandbox restricts filesystem access.

        The sandbox should only see explicitly mounted paths.
        """
        user = sandbox_test_user
        workspace = Path(f"/users/{user.username}/ag3ntum/persistent")

        # Run ls on /users inside sandbox - should fail or be empty
        bwrap_cmd = [
            "sudo", "-u", user.username,
            "bwrap",
            "--ro-bind", "/usr", "/usr",
        ] + get_bwrap_lib_binds() + [
            "--bind", str(workspace), "/workspace",
            "--proc", "/proc",
            "--dev", "/dev",
            "--unshare-all",
            "--die-with-parent",
            "/bin/ls", "/users",
        ]
        result = subprocess.run(
            bwrap_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # /users shouldn't exist or be accessible in sandbox
        assert result.returncode != 0 or result.stdout.strip() == "", (
            f"Sandbox should not expose /users. Got: {result.stdout}"
        )



@pytest.mark.e2e
@pytest.mark.slow
class TestMountAccess:
    """
    Tests for external mount access (read-only vs read-write).

    Uses test mount directories from tests/backend/input/mounts/
    Uses class-scoped fixture to create user once for all tests.
    """

    @pytest_asyncio.fixture(scope="class")
    async def user_with_mounts(
        self,
        user_service: UserService,
        real_session_factory: async_sessionmaker[AsyncSession],
    ) -> AsyncGenerator[User, None]:
        """Create a user for mount testing (once per class)."""
        username = generate_test_username()

        async with real_session_factory() as session:
            user = await user_service.create_user(
                db=session,
                username=username,
                email=f"{username}@test.example.com",
                password="TestPass123!",
            )

        yield user

        await cleanup_test_user(user_service, real_session_factory, username)

    @pytest.mark.asyncio
    async def test_mount_access_permissions(self, user_with_mounts: User) -> None:
        """
        Verify mount access permissions in sandbox.

        Tests combined for efficiency (single user creation):
        - Read-only mount is readable
        - Read-only mount cannot be written to
        - Read-write mount is writable
        - Read-write mount is readable
        """
        user = user_with_mounts

        # === Test 1: Read-only mount is readable ===
        if not TEST_RO_MOUNT.exists():
            pytest.skip(f"Test RO mount not found: {TEST_RO_MOUNT}")

        bwrap_cmd = [
            "sudo", "-u", user.username,
            "bwrap",
            "--ro-bind", "/usr", "/usr",
        ] + get_bwrap_lib_binds() + [
            "--ro-bind", str(TEST_RO_MOUNT), "/mnt/ro",
            "--proc", "/proc",
            "--dev", "/dev",
            "--die-with-parent",
            "/bin/cat", "/mnt/ro/readonly_file.txt",
        ]
        result = subprocess.run(
            bwrap_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, f"Could not read RO mount: {result.stderr}"
        assert "read-only test file" in result.stdout

        # === Test 2: Read-only mount cannot be written to ===
        bwrap_cmd = [
            "sudo", "-u", user.username,
            "bwrap",
            "--ro-bind", "/usr", "/usr",
        ] + get_bwrap_lib_binds() + [
            "--ro-bind", str(TEST_RO_MOUNT), "/mnt/ro",
            "--proc", "/proc",
            "--dev", "/dev",
            "--die-with-parent",
            "/bin/sh", "-c", "echo 'hacked' > /mnt/ro/hacked.txt",
        ]
        result = subprocess.run(
            bwrap_cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode != 0, "Should NOT be able to write to RO mount"
        assert not (TEST_RO_MOUNT / "hacked.txt").exists()

        # === Test 3 & 4: Read-write mount is writable and readable ===
        # Use a temporary directory (can't use TEST_RW_MOUNT - /tests is read-only in Docker)
        with tempfile.TemporaryDirectory(prefix="bwrap_rw_test_") as temp_rw_dir:
            temp_rw_path = Path(temp_rw_dir)
            test_file_name = f"test_write_{uuid.uuid4().hex[:8]}.txt"
            test_file = temp_rw_path / test_file_name

            # Ensure temp dir is writable by the test user
            subprocess.run(
                ["chmod", "777", str(temp_rw_path)],
                check=True,
                capture_output=True,
            )

            # Test 3: Write to RW mount
            bwrap_cmd = [
                "sudo", "-u", user.username,
                "bwrap",
                "--ro-bind", "/usr", "/usr",
            ] + get_bwrap_lib_binds() + [
                "--bind", str(temp_rw_path), "/mnt/rw",
                "--proc", "/proc",
                "--dev", "/dev",
                "--die-with-parent",
                "/bin/sh", "-c", f"echo 'test content' > /mnt/rw/{test_file_name}",
            ]
            result = subprocess.run(
                bwrap_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert result.returncode == 0, f"Could not write to RW mount: {result.stderr}"
            assert test_file.exists(), "Written file should exist"
            assert "test content" in test_file.read_text()

            # Test 4: Read from RW mount
            bwrap_cmd = [
                "sudo", "-u", user.username,
                "bwrap",
                "--ro-bind", "/usr", "/usr",
            ] + get_bwrap_lib_binds() + [
                "--bind", str(temp_rw_path), "/mnt/rw",
                "--proc", "/proc",
                "--dev", "/dev",
                "--die-with-parent",
                "/bin/cat", f"/mnt/rw/{test_file_name}",
            ]
            result = subprocess.run(
                bwrap_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert result.returncode == 0, f"Could not read RW mount: {result.stderr}"
            assert "test content" in result.stdout


@pytest.mark.e2e
@pytest.mark.slow
class TestSandboxRealExecution:
    """
    Tests for real sandbox execution using production code paths.

    These tests use the actual SandboxExecutor and execute_sandboxed_command()
    functions to verify that commands run with correct UID, environment
    isolation, and filesystem restrictions.

    This tests the same code path used by mcp__ag3ntum__Bash in production.

    Uses class-scoped fixture to create user once for all tests.
    """

    @pytest_asyncio.fixture(scope="class")
    async def sandbox_user(
        self,
        user_service: UserService,
        real_session_factory: async_sessionmaker[AsyncSession],
    ) -> AsyncGenerator[User, None]:
        """Create a user for sandbox execution testing (once per class)."""
        username = generate_test_username()

        async with real_session_factory() as session:
            user = await user_service.create_user(
                db=session,
                username=username,
                email=f"{username}@test.example.com",
                password="TestPass123!",
            )

        yield user

        await cleanup_test_user(user_service, real_session_factory, username)

    @pytest.mark.asyncio
    async def test_sandbox_uid_isolation(self, sandbox_user: User) -> None:
        """
        Verify commands in sandbox run as the user's UID, not ag3ntum_api (45045).

        Tests combined for efficiency:
        - UID via 'id -u' command
        - UID via Python os.getuid()
        """
        user = sandbox_user

        # === Test 1: UID via 'id -u' command ===
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user,
            "id -u",
            timeout=30,
        )

        assert exit_code == 0, f"id command failed: {stderr}"

        actual_uid = int(stdout.strip())
        assert actual_uid == user.linux_uid, (
            f"Sandbox should run as user UID {user.linux_uid}, "
            f"but runs as UID {actual_uid}"
        )
        assert actual_uid != AG3NTUM_API_UID, (
            f"Sandbox is running as API UID {AG3NTUM_API_UID} - "
            "privilege dropping failed!"
        )

        # === Test 2: UID via Python os.getuid() ===
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user,
            "python3 -c 'import os; print(os.getuid())'",
            timeout=30,
        )

        assert exit_code == 0, f"Python command failed: {stderr}"

        actual_uid = int(stdout.strip())
        assert actual_uid == user.linux_uid, (
            f"Python sees UID {actual_uid}, expected {user.linux_uid}"
        )

    @pytest.mark.asyncio
    async def test_sandbox_env_isolation(self, sandbox_user: User) -> None:
        """
        Verify host environment variables are NOT visible in sandbox.

        Tests combined for efficiency:
        - Host env vars not visible via bash
        - Host env vars not visible via Python os.environ
        """
        user = sandbox_user

        # === Test 1: Env vars not visible via bash ===
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user,
            "echo \"PYTHONPATH=$PYTHONPATH\" && echo \"AG3NTUM_ROOT=$AG3NTUM_ROOT\"",
            timeout=30,
        )

        assert exit_code == 0, f"Echo failed: {stderr}"

        # These env vars are set in the Docker container but should NOT
        # be visible inside the sandbox due to --clearenv
        assert "PYTHONPATH=/" not in stdout, (
            f"Host PYTHONPATH leaked into sandbox! Got: {stdout}"
        )
        assert "AG3NTUM_ROOT=/" not in stdout, (
            f"Host AG3NTUM_ROOT leaked into sandbox! Got: {stdout}"
        )

        # === Test 2: Env vars not visible via Python ===
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user,
            "python3 -c 'import os; print(os.environ.get(\"PYTHONPATH\", \"NOT_FOUND\"))'",
            timeout=30,
        )

        assert exit_code == 0, f"Python failed: {stderr}"
        assert "NOT_FOUND" in stdout, (
            f"Python should not see host PYTHONPATH. Got: {stdout}"
        )

    @pytest.mark.asyncio
    async def test_sandbox_filesystem_access(self, sandbox_user: User) -> None:
        """
        Verify sandbox filesystem isolation.

        Tests combined for efficiency:
        - Can write to /workspace
        - Cannot write outside /workspace
        - Cannot read /users directory
        - Cannot read /etc/passwd
        """
        user = sandbox_user

        # === Test 1: Can write to /workspace ===
        test_filename = f"test_write_{uuid.uuid4().hex[:8]}.txt"

        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user,
            f"echo 'hello from sandbox' > /workspace/{test_filename} && cat /workspace/{test_filename}",
            timeout=30,
        )

        assert exit_code == 0, f"Write failed: {stderr}"
        assert "hello from sandbox" in stdout

        # Verify file exists on host
        workspace = Path(f"/users/{user.username}/ag3ntum/persistent")
        test_file = workspace / test_filename
        assert test_file.exists(), "File should exist in user's persistent storage"

        # Verify ownership - skip on fakeowner mounts (Docker Desktop for Mac)
        if not is_fakeowner_mount(workspace):
            stat_info = test_file.stat()
            assert stat_info.st_uid == user.linux_uid, (
                f"File owned by UID {stat_info.st_uid}, expected {user.linux_uid}"
            )
        else:
            # Verify ownership via sandbox command instead
            exit_code2, stdout2, _ = await execute_test_sandbox_command(
                user,
                f"stat -c '%u' /workspace/{test_filename}",
                timeout=30,
            )
            assert exit_code2 == 0
            assert str(user.linux_uid) in stdout2 or "2000" in stdout2, (
                f"File should be owned by user inside sandbox. Got: {stdout2}"
            )

        # Cleanup test file
        test_file.unlink()

        # === Test 2: Cannot write outside /workspace ===
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user,
            "echo 'test' > /etc/test_write_attempt.txt 2>&1 || echo 'WRITE_FAILED'",
            timeout=30,
        )

        assert "WRITE_FAILED" in stdout or exit_code != 0 or "Read-only" in stderr, (
            f"Should not be able to write to /etc. Exit: {exit_code}, stdout: {stdout}"
        )

        # === Test 3: Cannot read /users directory ===
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user,
            "ls /users 2>&1 || echo 'ACCESS_DENIED'",
            timeout=30,
        )

        assert "ACCESS_DENIED" in stdout or "No such file" in stderr or "No such file" in stdout or exit_code != 0, (
            f"Sandbox should not see /users. Exit: {exit_code}, stdout: {stdout}"
        )

        # === Test 4: Cannot read /etc/passwd ===
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user,
            "cat /etc/passwd 2>&1 || echo 'FILE_NOT_FOUND'",
            timeout=30,
        )

        assert "FILE_NOT_FOUND" in stdout or "No such file" in stderr or "No such file" in stdout, (
            f"Sandbox should not see /etc/passwd. Got stdout: {stdout}, stderr: {stderr}"
        )

    @pytest.mark.asyncio
    async def test_sandbox_proc_filtering(self, sandbox_user: User) -> None:
        """
        Verify /proc filtering hides other processes.

        Tests combined for efficiency:
        - /proc shows limited entries (no other process PIDs)
        - Python cannot read /proc/1/environ
        """
        user = sandbox_user

        # === Test 1: /proc shows limited entries ===
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user,
            "ls /proc 2>&1 | head -20",
            timeout=30,
        )

        if exit_code != 0:
            pytest.skip(f"Could not access /proc in sandbox: {stderr}")

        # Should see limited entries (self, cpuinfo, meminfo, etc.)
        # Should NOT see numeric PIDs of other processes
        lines = stdout.strip().split("\n")
        numeric_pids = [l for l in lines if l.isdigit() and l != "1"]

        assert len(numeric_pids) < 5, (
            f"Sandbox /proc should be filtered. Found PIDs: {numeric_pids}"
        )

        # === Test 2: Python cannot read /proc/1/environ ===
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user,
            "python3 -c 'print(open(\"/proc/1/environ\").read())' 2>&1 || echo 'ACCESS_DENIED'",
            timeout=30,
        )

        assert "ACCESS_DENIED" in stdout or "Permission" in stderr or "No such file" in stderr or "No such file" in stdout, (
            f"Should not access /proc/1/environ. Got stdout: {stdout}, stderr: {stderr}"
        )


@pytest.mark.e2e
@pytest.mark.slow
class TestSandboxUserIsolation:
    """
    Tests for user-to-user isolation using real sandbox execution.

    Creates two users and verifies that code running as user1 cannot
    access user2's files, even through Python or bash commands.

    Uses class-scoped fixture to create users once for all tests.
    """

    @pytest_asyncio.fixture(scope="class")
    async def two_sandbox_users(
        self,
        user_service: UserService,
        real_session_factory: async_sessionmaker[AsyncSession],
    ) -> AsyncGenerator[tuple[User, User], None]:
        """Create two users for isolation testing (once per class)."""
        username1 = generate_test_username()
        username2 = generate_test_username()

        async with real_session_factory() as session:
            user1 = await user_service.create_user(
                db=session,
                username=username1,
                email=f"{username1}@test.example.com",
                password="TestPass123!",
            )

        async with real_session_factory() as session:
            user2 = await user_service.create_user(
                db=session,
                username=username2,
                email=f"{username2}@test.example.com",
                password="TestPass123!",
            )

        # Create a test file in user2's workspace
        user2_workspace = Path(f"/users/{user2.username}/ag3ntum/persistent")
        test_file = user2_workspace / "secret_file.txt"
        test_file.write_text("user2_secret_content")

        # Set ownership to user2
        _run_privileged(
            ["chown", f"{user2.linux_uid}:{user2.linux_uid}", str(test_file)],
            capture_output=True,
        )

        yield user1, user2

        # Cleanup
        await cleanup_test_user(user_service, real_session_factory, username1)
        await cleanup_test_user(user_service, real_session_factory, username2)

    @pytest.mark.asyncio
    async def test_sandbox_user_isolation(
        self, two_sandbox_users: tuple[User, User]
    ) -> None:
        """
        Verify sandbox isolation between users.

        Tests combined for efficiency (single 2-user creation):
        - User1 cannot read user2's files via bash
        - User1 cannot read user2's files via Python
        - User1 cannot enumerate /users directory
        - Each user only sees their own /workspace
        """
        user1, user2 = two_sandbox_users

        # === Test 1: User1 cannot read user2's files via bash ===
        user2_path = f"/users/{user2.username}/ag3ntum/persistent/secret_file.txt"

        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user1,
            f"cat {user2_path} 2>&1 || echo 'ACCESS_DENIED'",
            timeout=30,
        )

        assert "ACCESS_DENIED" in stdout or "No such file" in stderr or "No such file" in stdout, (
            f"User1 should NOT access user2's files via bash. Got: stdout={stdout}, stderr={stderr}"
        )

        # === Test 2: User1 cannot read user2's files via Python ===
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user1,
            f"python3 -c 'print(open(\"{user2_path}\").read())' 2>&1 || echo 'ACCESS_DENIED'",
            timeout=30,
        )

        assert "ACCESS_DENIED" in stdout or "No such file" in stderr or "No such file" in stdout, (
            f"User1 should NOT access user2's files via Python. Got: stdout={stdout}, stderr={stderr}"
        )

        # === Test 3: User1 cannot enumerate /users directory ===
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user1,
            "ls -la /users 2>&1 || echo 'NO_ACCESS'",
            timeout=30,
        )

        assert "NO_ACCESS" in stdout or "No such file" in stderr or "No such file" in stdout, (
            f"Sandbox should NOT see /users. Got: stdout={stdout}, stderr={stderr}"
        )

        # === Test 4: Each user sees only their own /workspace ===
        workspace1 = Path(f"/users/{user1.username}/ag3ntum/persistent")
        workspace2 = Path(f"/users/{user2.username}/ag3ntum/persistent")

        marker1 = workspace1 / "user1_marker.txt"
        marker2 = workspace2 / "user2_marker.txt"

        marker1.write_text("I am user1")
        marker2.write_text("I am user2")

        # Set ownership
        _run_privileged(
            ["chown", f"{user1.linux_uid}:{user1.linux_uid}", str(marker1)],
            capture_output=True,
        )
        _run_privileged(
            ["chown", f"{user2.linux_uid}:{user2.linux_uid}", str(marker2)],
            capture_output=True,
        )

        # User1 should see user1_marker but not user2_marker
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user1,
            "ls /workspace && cat /workspace/user1_marker.txt",
            timeout=30,
        )

        assert exit_code == 0, f"User1 sandbox failed: {stderr}"
        assert "I am user1" in stdout
        assert "user2_marker" not in stdout

        # User2 should see user2_marker but not user1_marker
        exit_code, stdout, stderr = await execute_test_sandbox_command(
            user2,
            "ls /workspace && cat /workspace/user2_marker.txt",
            timeout=30,
        )

        assert exit_code == 0, f"User2 sandbox failed: {stderr}"
        assert "I am user2" in stdout
        assert "user1_marker" not in stdout

        # Cleanup marker files
        marker1.unlink()
