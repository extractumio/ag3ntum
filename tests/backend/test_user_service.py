"""
Unit tests for UserService.

Tests cover:
- Username validation (format, uniqueness)
- Password hashing with bcrypt
- JWT secret generation
- UID generation (sequential from 2000)
- Error handling for duplicate users
- _setup_group_permissions phase separation (usermod vs chmod/chgrp)
- _delete_linux_user group cleanup after userdel
- _create_linux_user stale entry detection and retry on useradd code 9
"""
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.services.user_service import UserService


class TestUsernameValidation:
    """Test username format validation."""

    @pytest.fixture
    def user_service(self) -> UserService:
        return UserService()

    def test_valid_username_lowercase(self, user_service: UserService) -> None:
        """Valid lowercase username passes validation."""
        assert user_service._validate_username("testuser") is True

    def test_valid_username_with_numbers(self, user_service: UserService) -> None:
        """Username with numbers passes validation."""
        assert user_service._validate_username("user123") is True

    def test_valid_username_with_underscore(self, user_service: UserService) -> None:
        """Username with underscore passes validation."""
        assert user_service._validate_username("test_user") is True

    def test_valid_username_starts_with_underscore(self, user_service: UserService) -> None:
        """Username starting with underscore is valid (Linux convention)."""
        assert user_service._validate_username("_testuser") is True

    def test_valid_username_minimum_length(self, user_service: UserService) -> None:
        """Username with minimum 3 characters passes."""
        assert user_service._validate_username("abc") is True

    def test_valid_username_maximum_length(self, user_service: UserService) -> None:
        """Username with maximum 32 characters passes."""
        assert user_service._validate_username("a" * 32) is True

    def test_invalid_username_too_short(self, user_service: UserService) -> None:
        """Username with less than 3 characters fails."""
        assert user_service._validate_username("ab") is False

    def test_invalid_username_too_long(self, user_service: UserService) -> None:
        """Username with more than 32 characters fails."""
        assert user_service._validate_username("a" * 33) is False

    def test_invalid_username_starts_with_number(self, user_service: UserService) -> None:
        """Username starting with number fails (Linux constraint)."""
        assert user_service._validate_username("1user") is False

    def test_invalid_username_uppercase(self, user_service: UserService) -> None:
        """Username with uppercase fails (Linux convention)."""
        assert user_service._validate_username("TestUser") is False

    def test_invalid_username_special_chars(self, user_service: UserService) -> None:
        """Username with special characters fails."""
        assert user_service._validate_username("user@name") is False
        assert user_service._validate_username("user-name") is False
        assert user_service._validate_username("user.name") is False

    def test_invalid_username_empty(self, user_service: UserService) -> None:
        """Empty username fails validation."""
        assert user_service._validate_username("") is False

    def test_invalid_username_spaces(self, user_service: UserService) -> None:
        """Username with spaces fails."""
        assert user_service._validate_username("test user") is False


class TestUIDGeneration:
    """Test Linux UID generation.

    UIDs are allocated based on the configured mode:
    - ISOLATED mode (default): UIDs from 50000-60000
    - DIRECT mode: UIDs from 1000-65533

    Legacy UIDs (2000-49999) from older installations are still valid
    but new allocations start at 50000 in isolated mode.
    """

    @pytest.fixture
    def user_service(self) -> UserService:
        return UserService()

    @pytest.mark.asyncio
    async def test_first_uid_is_50000_isolated_mode(self, user_service: UserService) -> None:
        """First generated UID should be 50000 in isolated mode (default)."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Mock _get_system_uids_in_range to return empty set (no system users)
        with patch.object(user_service, '_get_system_uids_in_range', return_value=set()):
            uid = await user_service._generate_next_uid(mock_session)
        # Default isolated mode starts at 50000
        assert uid == 50000

    @pytest.mark.asyncio
    async def test_uid_increments_from_existing(self, user_service: UserService) -> None:
        """UID increments from highest existing UID in range."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 50005
        mock_session.execute.return_value = mock_result

        # Mock _get_system_uids_in_range to return empty set (no system users)
        with patch.object(user_service, '_get_system_uids_in_range', return_value=set()):
            uid = await user_service._generate_next_uid(mock_session)
        assert uid == 50006

    @pytest.mark.asyncio
    async def test_uid_starts_at_range_min_if_no_existing_in_range(self, user_service: UserService) -> None:
        """UID starts at range minimum if no existing UIDs in current range.

        Even if legacy UIDs exist below the range, new allocations start
        at the configured minimum (50000 for isolated mode).
        """
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        # Query for UIDs in 50000-60000 range returns None (no existing users in range)
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Mock _get_system_uids_in_range to return empty set (no system users)
        with patch.object(user_service, '_get_system_uids_in_range', return_value=set()):
            uid = await user_service._generate_next_uid(mock_session)
        # Should start at range minimum regardless of legacy UIDs
        assert uid == 50000


class TestCreateUser:
    """Test user creation flow."""

    @pytest.fixture
    def user_service(self) -> UserService:
        return UserService()

    @pytest.mark.asyncio
    async def test_create_user_invalid_username_raises(self, user_service: UserService) -> None:
        """Creating user with invalid username raises ValueError."""
        mock_session = AsyncMock(spec=AsyncSession)

        with pytest.raises(ValueError, match="Invalid username"):
            await user_service.create_user(
                db=mock_session,
                username="123invalid",  # Starts with number
                email="test@example.com",
                password="password123",
            )

    @pytest.mark.asyncio
    async def test_create_user_duplicate_username_raises(self, user_service: UserService) -> None:
        """Creating user with existing username raises ValueError."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()  # Existing user
        mock_session.execute.return_value = mock_result

        with pytest.raises(ValueError, match="already exists"):
            await user_service.create_user(
                db=mock_session,
                username="existinguser",
                email="new@example.com",
                password="password123",
            )

    @pytest.mark.asyncio
    async def test_create_user_duplicate_email_raises(self, user_service: UserService) -> None:
        """Creating user with existing email raises ValueError."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()  # Existing user
        mock_session.execute.return_value = mock_result

        with pytest.raises(ValueError, match="already exists"):
            await user_service.create_user(
                db=mock_session,
                username="newuser",
                email="existing@example.com",
                password="password123",
            )

    @pytest.mark.asyncio
    async def test_create_user_generates_jwt_secret(self, user_service: UserService) -> None:
        """User creation generates a per-user JWT secret."""
        mock_session = AsyncMock(spec=AsyncSession)

        # No existing user
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Mock Linux user creation
        with patch.object(user_service, '_create_linux_user'):
            with patch.object(user_service, '_generate_next_uid', return_value=2000):
                # Capture the user object that gets added
                added_user = None
                def capture_add(user):
                    nonlocal added_user
                    added_user = user
                mock_session.add = capture_add
                mock_session.refresh = AsyncMock()

                await user_service.create_user(
                    db=mock_session,
                    username="testuser",
                    email="test@example.com",
                    password="password123",
                )

                # JWT secret should be generated (URL-safe base64, 32 bytes = 43 chars)
                assert added_user is not None
                assert added_user.jwt_secret is not None
                assert len(added_user.jwt_secret) >= 40  # token_urlsafe(32) produces ~43 chars

    @pytest.mark.asyncio
    async def test_create_user_hashes_password(self, user_service: UserService) -> None:
        """User creation hashes the password with bcrypt."""
        mock_session = AsyncMock(spec=AsyncSession)

        # No existing user
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Mock Linux user creation
        with patch.object(user_service, '_create_linux_user'):
            with patch.object(user_service, '_generate_next_uid', return_value=2000):
                added_user = None
                def capture_add(user):
                    nonlocal added_user
                    added_user = user
                mock_session.add = capture_add
                mock_session.refresh = AsyncMock()

                await user_service.create_user(
                    db=mock_session,
                    username="testuser",
                    email="test@example.com",
                    password="mypassword",
                )

                # Password should be hashed (not plaintext)
                assert added_user is not None
                assert added_user.password_hash != "mypassword"
                # Bcrypt hashes start with $2b$
                assert added_user.password_hash.startswith("$2")

    @pytest.mark.asyncio
    async def test_create_user_default_role_is_user(self, user_service: UserService) -> None:
        """Default role for new users is 'user'."""
        mock_session = AsyncMock(spec=AsyncSession)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with patch.object(user_service, '_create_linux_user'):
            with patch.object(user_service, '_generate_next_uid', return_value=2000):
                added_user = None
                def capture_add(user):
                    nonlocal added_user
                    added_user = user
                mock_session.add = capture_add
                mock_session.refresh = AsyncMock()

                await user_service.create_user(
                    db=mock_session,
                    username="testuser",
                    email="test@example.com",
                    password="password123",
                )

                assert added_user.role == "user"

    @pytest.mark.asyncio
    async def test_create_user_custom_role(self, user_service: UserService) -> None:
        """Users can be created with custom role."""
        mock_session = AsyncMock(spec=AsyncSession)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        with patch.object(user_service, '_create_linux_user'):
            with patch.object(user_service, '_generate_next_uid', return_value=2000):
                added_user = None
                def capture_add(user):
                    nonlocal added_user
                    added_user = user
                mock_session.add = capture_add
                mock_session.refresh = AsyncMock()

                await user_service.create_user(
                    db=mock_session,
                    username="adminuser",
                    email="admin@example.com",
                    password="password123",
                    role="admin",
                )

                assert added_user.role == "admin"


class TestLinuxUserCreation:
    """Test Linux user creation via subprocess."""

    @pytest.fixture
    def user_service(self) -> UserService:
        return UserService()

    def test_linux_user_creation_calls_subprocess(self, user_service: UserService) -> None:
        """Linux user creation runs useradd via sudo."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('pathlib.Path.mkdir'):
                with patch('pathlib.Path.chmod'):
                    with patch('pathlib.Path.write_text'):
                        with patch('pathlib.Path.exists', return_value=False):
                            with patch.object(user_service, '_create_user_venv'):
                                with patch.object(user_service, '_create_user_secrets'):
                                    user_service._create_linux_user("testuser", 2000)

                                    # Should have called subprocess.run at least once
                                    assert mock_run.called
                                    # Check for useradd call (might be 3 calls: chown, chmod, useradd, chown)
                                    calls = mock_run.call_args_list
                                    useradd_call = [c for c in calls if 'useradd' in str(c)]
                                    assert len(useradd_call) > 0

    def test_linux_user_creation_uses_correct_uid(self, user_service: UserService) -> None:
        """Linux user creation uses the provided UID."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch('pathlib.Path.mkdir'):
                with patch('pathlib.Path.chmod'):
                    with patch('pathlib.Path.write_text'):
                        with patch('pathlib.Path.exists', return_value=False):
                            with patch.object(user_service, '_create_user_venv'):
                                with patch.object(user_service, '_create_user_secrets'):
                                    user_service._create_linux_user("testuser", 2005)

                                    # Find the useradd call
                                    calls = [str(c) for c in mock_run.call_args_list]
                                    useradd_calls = [c for c in calls if 'useradd' in c]
                                    assert any('2005' in c for c in useradd_calls)

    def test_linux_user_creation_failure_raises(self, user_service: UserService) -> None:
        """Failed Linux user creation raises ValueError."""
        with patch('subprocess.run') as mock_run:
            # First call (chown) succeeds, useradd fails
            mock_run.side_effect = [
                MagicMock(returncode=0),  # First chown
                subprocess.CalledProcessError(1, 'useradd', stderr=b'error')
            ]
            with patch('pathlib.Path.mkdir'):
                with patch('pathlib.Path.chmod'):
                    with patch('pathlib.Path.write_text'):
                        with patch('pathlib.Path.exists', return_value=False):
                            # Mock helper methods that also call subprocess
                            with patch.object(user_service, '_create_user_venv'):
                                with patch.object(user_service, '_create_user_secrets'):
                                    with pytest.raises(ValueError, match="Failed to"):
                                        user_service._create_linux_user("testuser", 2000)


class TestSetupGroupPermissionsPhases:
    """Test that _setup_group_permissions separates usermod from chmod/chgrp.

    The critical fix: when usermod fails (e.g., stale user entries after
    delete/recreate), the chmod/chgrp operations must still run. Without
    this, the home directory stays at 700 (owner-only), preventing the API
    from accessing it and blocking login with UserEnvironmentError.
    """

    @pytest.fixture
    def user_service(self) -> UserService:
        return UserService()

    def test_chmod_runs_even_when_usermod_fails(self, user_service: UserService) -> None:
        """File permissions (chmod/chgrp) are set even when usermod fails.

        This is THE core regression test: before the fix, a single try/except
        wrapped both usermod and chmod/chgrp. If usermod failed, all file
        permission operations were skipped, leaving home dir at 700.
        """
        home_dir = Path("/users/testuser")
        calls_made = []

        def mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            calls_made.append(cmd_str)

            # Both usermod calls fail
            if "usermod" in cmd_str:
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(
                        6, cmd, stderr=b"usermod: user 'testuser' does not exist"
                    )
                return MagicMock(returncode=6, stderr=b"usermod: user 'testuser' does not exist")

            # All other commands succeed
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with patch("pathlib.Path.exists", return_value=True):
                user_service._setup_group_permissions(home_dir, 50000, "testuser")

        # Both usermod calls should have been attempted
        usermod_calls = [c for c in calls_made if "usermod" in c]
        assert len(usermod_calls) == 2, f"Expected 2 usermod calls, got {usermod_calls}"

        # Critical: chgrp and chmod MUST have been called despite usermod failures
        chgrp_calls = [c for c in calls_made if "chgrp" in c]
        chmod_calls = [c for c in calls_made if "chmod" in c]
        assert len(chgrp_calls) > 0, "chgrp should run even when usermod fails"
        assert len(chmod_calls) > 0, "chmod should run even when usermod fails"

        # Specifically check that home dir got 750 permissions
        chmod_750_home = [c for c in calls_made if "chmod 750" in c and str(home_dir) in c]
        assert len(chmod_750_home) > 0, "Home dir should get chmod 750 even when usermod fails"

    def test_chmod_runs_when_first_usermod_fails_second_succeeds(self, user_service: UserService) -> None:
        """File permissions set when only one usermod fails."""
        home_dir = Path("/users/testuser")
        calls_made = []
        usermod_count = [0]

        def mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            calls_made.append(cmd_str)

            if "usermod" in cmd_str:
                usermod_count[0] += 1
                if usermod_count[0] == 1 and "ag3ntum" in cmd_str and "testuser" not in cmd_str.split()[-1:]:
                    # First usermod (add user to ag3ntum) fails
                    if kwargs.get("check"):
                        raise subprocess.CalledProcessError(
                            6, cmd, stderr=b"usermod: user does not exist"
                        )
                # Second usermod succeeds
                return MagicMock(returncode=0)

            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with patch("pathlib.Path.exists", return_value=True):
                user_service._setup_group_permissions(home_dir, 50000, "testuser")

        # File permissions should still be set
        chgrp_calls = [c for c in calls_made if "chgrp" in c]
        assert len(chgrp_calls) > 0, "chgrp should run regardless of usermod results"

    def test_all_operations_succeed_in_normal_case(self, user_service: UserService) -> None:
        """When everything succeeds, all operations run."""
        home_dir = Path("/users/testuser")
        calls_made = []

        def mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            calls_made.append(cmd_str)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with patch("pathlib.Path.exists", return_value=True):
                user_service._setup_group_permissions(home_dir, 50000, "testuser")

        # All operations should have been called
        usermod_calls = [c for c in calls_made if "usermod" in c]
        chgrp_calls = [c for c in calls_made if "chgrp" in c]
        chmod_calls = [c for c in calls_made if "chmod" in c]

        assert len(usermod_calls) == 2, "Both usermod calls should run"
        assert len(chgrp_calls) >= 3, "Multiple chgrp calls expected (home, subdirs, ag3ntum)"
        assert len(chmod_calls) >= 3, "Multiple chmod calls expected"

    def test_home_dir_gets_ag3ntum_group_even_if_usermod_fails(self, user_service: UserService) -> None:
        """Home dir gets chgrp ag3ntum even when usermod fails.

        This ensures validate_user_environment() can access the home dir
        because ag3ntum_api is always in the ag3ntum group.
        """
        home_dir = Path("/users/testuser")
        calls_made = []

        def mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            calls_made.append(cmd_str)
            if "usermod" in cmd_str and kwargs.get("check"):
                raise subprocess.CalledProcessError(6, cmd, stderr=b"user does not exist")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with patch("pathlib.Path.exists", return_value=True):
                user_service._setup_group_permissions(home_dir, 50000, "testuser")

        # Check for chgrp ag3ntum on home dir specifically
        chgrp_home = [
            c for c in calls_made
            if "chgrp ag3ntum" in c and str(home_dir) in c and "-R" not in c
        ]
        assert len(chgrp_home) > 0, f"Home dir should get chgrp ag3ntum. Calls: {calls_made}"

    def test_sessions_dir_gets_770_even_if_usermod_fails(self, user_service: UserService) -> None:
        """Sessions dir gets 770 permissions even when usermod fails."""
        home_dir = Path("/users/testuser")
        calls_made = []

        def mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            calls_made.append(cmd_str)
            if "usermod" in cmd_str and kwargs.get("check"):
                raise subprocess.CalledProcessError(6, cmd, stderr=b"user does not exist")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with patch("pathlib.Path.exists", return_value=True):
                user_service._setup_group_permissions(home_dir, 50000, "testuser")

        chmod_sessions = [c for c in calls_made if "chmod" in c and "770" in c and "sessions" in c]
        assert len(chmod_sessions) > 0, "Sessions dir should get chmod 770"


class TestDeleteLinuxUserGroupCleanup:
    """Test that _delete_linux_user cleans up the group entry after userdel.

    Without group cleanup, stale group entries cause useradd to return
    code 9 ('username already in use') even when the user was deleted,
    because useradd checks /etc/group in addition to /etc/passwd.
    """

    @pytest.fixture
    def user_service(self) -> UserService:
        return UserService()

    def test_groupdel_called_after_successful_userdel(self, user_service: UserService) -> None:
        """groupdel is called after userdel succeeds."""
        calls_made = []

        def mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            calls_made.append(cmd_str)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            user_service._delete_linux_user("testuser")

        userdel_calls = [c for c in calls_made if "userdel" in c]
        groupdel_calls = [c for c in calls_made if "groupdel" in c]

        assert len(userdel_calls) == 1, "userdel should be called once"
        assert len(groupdel_calls) == 1, "groupdel should be called after userdel"
        assert "testuser" in groupdel_calls[0], "groupdel should target the username"

        # Verify order: userdel before groupdel
        userdel_idx = calls_made.index(userdel_calls[0])
        groupdel_idx = calls_made.index(groupdel_calls[0])
        assert userdel_idx < groupdel_idx, "userdel should run before groupdel"

    def test_groupdel_called_after_userdel_returns_code_6(self, user_service: UserService) -> None:
        """groupdel is called even when userdel returns 6 (user doesn't exist).

        The group might still exist even if the user doesn't.
        """
        calls_made = []

        def mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            calls_made.append(cmd_str)
            if "userdel" in cmd_str and kwargs.get("check"):
                raise subprocess.CalledProcessError(6, cmd, stderr=b"user does not exist")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            user_service._delete_linux_user("testuser")

        groupdel_calls = [c for c in calls_made if "groupdel" in c]
        assert len(groupdel_calls) == 1, "groupdel should run even when user doesn't exist"

    def test_groupdel_failure_is_silently_ignored(self, user_service: UserService) -> None:
        """groupdel failure doesn't raise (group may not exist or have members)."""
        def mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "groupdel" in cmd_str and kwargs.get("check"):
                raise subprocess.CalledProcessError(6, cmd, stderr=b"group does not exist")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            # Should not raise
            user_service._delete_linux_user("testuser")

    def test_userdel_non6_failure_still_raises(self, user_service: UserService) -> None:
        """userdel failure with code != 6 still raises (e.g., user logged in)."""
        def mock_subprocess_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "userdel" in cmd_str and kwargs.get("check"):
                raise subprocess.CalledProcessError(8, cmd, stderr=b"user is currently logged in")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            with pytest.raises(subprocess.CalledProcessError):
                user_service._delete_linux_user("testuser")


class TestCreateLinuxUserStaleEntryRetry:
    """Test _create_linux_user handles stale entries on useradd code 9.

    When useradd returns 9 ('username already in use'), the code now verifies
    the user actually exists in /etc/passwd via getent. If not (stale shadow
    or group entries only), it cleans up and retries useradd.
    """

    @pytest.fixture
    def user_service(self) -> UserService:
        return UserService()

    def _make_mock_run(self, useradd_behavior="success", getent_found=True, retry_succeeds=True):
        """Create a mock subprocess.run that simulates various scenarios.

        Args:
            useradd_behavior: "success", "code9", or "other_error"
            getent_found: Whether getent finds the user in /etc/passwd
            retry_succeeds: Whether retry useradd succeeds
        """
        useradd_calls = [0]

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)

            if "useradd" in cmd_str:
                useradd_calls[0] += 1
                if useradd_calls[0] == 1 and useradd_behavior == "code9":
                    if kwargs.get("check"):
                        raise subprocess.CalledProcessError(
                            9, cmd, stderr=b"useradd: user 'testuser' already exists"
                        )
                    return MagicMock(returncode=9, stderr=b"already exists")
                elif useradd_calls[0] == 2:
                    # Retry
                    rc = 0 if retry_succeeds else 1
                    return MagicMock(returncode=rc, stderr=b"error" if rc else b"")
                return MagicMock(returncode=0)

            if "getent" in cmd_str and "passwd" in cmd_str:
                rc = 0 if getent_found else 2
                return MagicMock(returncode=rc, stdout=b"testuser:x:50000:50000::/users/testuser:/bin/bash" if getent_found else b"")

            if "groupdel" in cmd_str:
                return MagicMock(returncode=0)

            if "sed" in cmd_str:
                return MagicMock(returncode=0)

            # All other commands (chown, chmod, etc.) succeed
            return MagicMock(returncode=0)

        return mock_run

    def test_code9_user_exists_proceeds_normally(self, user_service: UserService) -> None:
        """When useradd returns 9 and user exists in /etc/passwd, proceed."""
        mock_run = self._make_mock_run(useradd_behavior="code9", getent_found=True)

        with patch("subprocess.run", side_effect=mock_run):
            with patch("pathlib.Path.mkdir"):
                with patch("pathlib.Path.chmod"):
                    with patch("pathlib.Path.write_text"):
                        with patch("pathlib.Path.exists", return_value=False):
                            with patch.object(user_service, "_create_user_venv"):
                                with patch.object(user_service, "_create_user_secrets"):
                                    with patch.object(user_service, "_setup_group_permissions"):
                                        with patch(
                                            "src.services.user_service.refresh_process_supplementary_groups"
                                        ):
                                            # Should not raise
                                            user_service._create_linux_user("testuser", 50000)

    def test_code9_stale_entry_cleans_up_and_retries(self, user_service: UserService) -> None:
        """When useradd returns 9 but user NOT in /etc/passwd, cleanup and retry."""
        calls_made = []
        useradd_calls = [0]

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            calls_made.append(cmd_str)

            if "useradd" in cmd_str:
                useradd_calls[0] += 1
                if useradd_calls[0] == 1 and kwargs.get("check"):
                    raise subprocess.CalledProcessError(
                        9, cmd, stderr=b"useradd: user already exists"
                    )
                # Retry succeeds
                return MagicMock(returncode=0)

            if "getent" in cmd_str and "passwd" in cmd_str:
                # User NOT found in /etc/passwd
                return MagicMock(returncode=2, stdout=b"")

            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_run):
            with patch("pathlib.Path.mkdir"):
                with patch("pathlib.Path.chmod"):
                    with patch("pathlib.Path.write_text"):
                        with patch("pathlib.Path.exists", return_value=False):
                            with patch.object(user_service, "_create_user_venv"):
                                with patch.object(user_service, "_create_user_secrets"):
                                    with patch.object(user_service, "_setup_group_permissions"):
                                        with patch(
                                            "src.services.user_service.refresh_process_supplementary_groups"
                                        ):
                                            user_service._create_linux_user("testuser", 50000)

        # Verify cleanup operations were called
        groupdel_calls = [c for c in calls_made if "groupdel" in c]
        sed_calls = [c for c in calls_made if "sed" in c and "shadow" in c]
        assert len(groupdel_calls) >= 1, "groupdel should be called for cleanup"
        assert len(sed_calls) >= 1, "sed on /etc/shadow should be called for cleanup"

        # Verify retry useradd was called
        useradd_cmds = [c for c in calls_made if "useradd" in c]
        assert len(useradd_cmds) == 2, f"useradd should be called twice (initial + retry), got: {useradd_cmds}"

    def test_code9_stale_group_falls_back_to_g_flag(self, user_service: UserService) -> None:
        """When group still exists after cleanup, retry with -g to adopt existing group."""
        calls_made = []
        useradd_calls = [0]

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            calls_made.append(cmd_str)

            if "useradd" in cmd_str:
                useradd_calls[0] += 1
                if useradd_calls[0] == 1 and kwargs.get("check"):
                    raise subprocess.CalledProcessError(
                        9, cmd, stderr=b"useradd: user already exists"
                    )
                if useradd_calls[0] == 2:
                    # First retry fails because group still exists
                    return MagicMock(returncode=9, stderr=b"useradd: group testuser exists - if you want to add this user to that group, use -g.")
                # Third call with -g flag succeeds
                return MagicMock(returncode=0)

            if "getent" in cmd_str and "passwd" in cmd_str:
                return MagicMock(returncode=2, stdout=b"")

            if "getent" in cmd_str and "group" in cmd_str:
                return MagicMock(returncode=0, stdout="testuser:x:50000:\n")

            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_run):
            with patch("pathlib.Path.mkdir"):
                with patch("pathlib.Path.chmod"):
                    with patch("pathlib.Path.write_text"):
                        with patch("pathlib.Path.exists", return_value=False):
                            with patch.object(user_service, "_create_user_venv"):
                                with patch.object(user_service, "_create_user_secrets"):
                                    with patch.object(user_service, "_setup_group_permissions"):
                                        with patch(
                                            "src.services.user_service.refresh_process_supplementary_groups"
                                        ):
                                            user_service._create_linux_user("testuser", 50000)

        # Verify the -g flag was used in the final retry
        useradd_with_g = [c for c in calls_made if "useradd" in c and "-g" in c]
        assert len(useradd_with_g) == 1, f"Should have one useradd call with -g flag. Calls: {[c for c in calls_made if 'useradd' in c]}"

    def test_code9_stale_entry_retry_fails_raises(self, user_service: UserService) -> None:
        """When stale entry cleanup fails to resolve, raises ValueError."""
        useradd_calls = [0]

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)

            if "useradd" in cmd_str:
                useradd_calls[0] += 1
                if useradd_calls[0] == 1 and kwargs.get("check"):
                    raise subprocess.CalledProcessError(
                        9, cmd, stderr=b"useradd: user already exists"
                    )
                # Retry also fails (non-group-related error)
                return MagicMock(returncode=1, stderr=b"retry failed")

            if "getent" in cmd_str and "passwd" in cmd_str:
                return MagicMock(returncode=2, stdout=b"")

            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_run):
            with patch("pathlib.Path.mkdir"):
                with patch("pathlib.Path.chmod"):
                    with patch("pathlib.Path.write_text"):
                        with patch("pathlib.Path.exists", return_value=False):
                            with patch.object(user_service, "_create_user_venv"):
                                with patch.object(user_service, "_create_user_secrets"):
                                    with pytest.raises(ValueError, match="Failed to create Linux user after cleanup"):
                                        user_service._create_linux_user("testuser", 50000)

    def test_useradd_other_error_still_raises(self, user_service: UserService) -> None:
        """Non-code-9 useradd errors still raise ValueError."""
        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "useradd" in cmd_str and kwargs.get("check"):
                raise subprocess.CalledProcessError(1, cmd, stderr=b"generic error")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_run):
            with patch("pathlib.Path.mkdir"):
                with patch("pathlib.Path.chmod"):
                    with patch("pathlib.Path.write_text"):
                        with patch("pathlib.Path.exists", return_value=False):
                            with patch.object(user_service, "_create_user_venv"):
                                with patch.object(user_service, "_create_user_secrets"):
                                    with pytest.raises(ValueError, match="Failed to create Linux user"):
                                        user_service._create_linux_user("testuser", 50000)

    def test_normal_useradd_success_no_getent_check(self, user_service: UserService) -> None:
        """When useradd succeeds, no getent verification is needed."""
        calls_made = []

        def mock_run(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            calls_made.append(cmd_str)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_run):
            with patch("pathlib.Path.mkdir"):
                with patch("pathlib.Path.chmod"):
                    with patch("pathlib.Path.write_text"):
                        with patch("pathlib.Path.exists", return_value=False):
                            with patch.object(user_service, "_create_user_venv"):
                                with patch.object(user_service, "_create_user_secrets"):
                                    with patch.object(user_service, "_setup_group_permissions"):
                                        with patch(
                                            "src.services.user_service.refresh_process_supplementary_groups"
                                        ):
                                            user_service._create_linux_user("testuser", 50000)

        getent_calls = [c for c in calls_made if "getent" in c]
        assert len(getent_calls) == 0, "getent should not be called when useradd succeeds"
