"""
Unit tests for UserService.

Tests cover:
- Username validation (format, uniqueness)
- Password hashing with bcrypt
- JWT secret generation
- UID generation (sequential from 2000)
- Error handling for duplicate users
"""
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
    """Test Linux UID generation."""

    @pytest.fixture
    def user_service(self) -> UserService:
        return UserService()

    @pytest.mark.asyncio
    async def test_first_uid_is_2000(self, user_service: UserService) -> None:
        """First generated UID should be 2000."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        uid = await user_service._generate_next_uid(mock_session)
        assert uid == 2000

    @pytest.mark.asyncio
    async def test_uid_increments_from_existing(self, user_service: UserService) -> None:
        """UID increments from highest existing UID."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 2005
        mock_session.execute.return_value = mock_result

        uid = await user_service._generate_next_uid(mock_session)
        assert uid == 2006

    @pytest.mark.asyncio
    async def test_uid_starts_at_2000_if_legacy_low_uid(self, user_service: UserService) -> None:
        """UID starts at 2000 if existing UIDs are below 2000 (legacy)."""
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = 1500  # Legacy UID
        mock_session.execute.return_value = mock_result

        uid = await user_service._generate_next_uid(mock_session)
        assert uid == 2000


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
        import subprocess

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
