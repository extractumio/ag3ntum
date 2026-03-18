"""Feature flag inheritance: platform -> reseller -> user."""
import asyncio
import json
import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Platform-level defaults (baseline)
DEFAULT_FEATURES: dict[str, Any] = {
    "ssh_enabled": False,
    "file_upload_enabled": True,
    "file_download_enabled": True,
    "max_session_minutes": 30,
    "max_turns_per_session": 50,
    "allowed_models": ["claude-sonnet-4-20250514"],
    "web_fetch_enabled": False,
    "custom_skills_enabled": False,
    "vault_enabled": True,
    "max_ssh_connections": 3,
    "allowed_tools": None,
    "disabled_tools": [],
    "enabled_skills": None,
    "max_custom_skills": 10,
    "skill_upload_enabled": False,
}

# Default quotas (baseline)
DEFAULT_QUOTAS: dict[str, Any] = {
    "global_max_concurrent": 4,
    "per_user_max_concurrent": 2,
    "per_user_daily_limit": 50,
}

# Default spending limits (baseline)
DEFAULT_SPENDING: dict[str, Any] = {
    "reseller_monthly_usd": None,
    "reseller_daily_usd": None,
    "user_monthly_usd": None,
    "user_daily_usd": None,
    "user_per_session_usd": None,
}


class FeatureFlagService:
    """Three-tier feature flag resolution with DB-backed platform defaults."""

    # Section name → (hardcoded defaults dict, private cache attr name)
    _SECTIONS: dict[str, tuple[dict[str, Any], str]] = {
        "features": (DEFAULT_FEATURES, "_db_features"),
        "quotas": (DEFAULT_QUOTAS, "_db_quotas"),
        "spending": (DEFAULT_SPENDING, "_db_spending"),
        "retention": ({}, "_db_retention"),
    }

    def __init__(self) -> None:
        self._db_features: dict[str, Any] = {}
        self._db_quotas: dict[str, Any] = {}
        self._db_spending: dict[str, Any] = {}
        self._db_retention: dict[str, Any] = {}
        self._loaded = False
        self._load_lock = asyncio.Lock()

    async def ensure_loaded(self, db: AsyncSession) -> None:
        """Idempotent load — call from endpoints to guarantee init."""
        if self._loaded:
            return
        async with self._load_lock:
            if not self._loaded:  # double-check after acquiring lock
                await self.load_platform_defaults(db)

    async def load_platform_defaults(self, db: AsyncSession) -> None:
        """Load platform defaults from the platform_config table.

        Call once at startup (or after admin updates). Overrides
        the hardcoded DEFAULT_* dicts with DB-stored values.
        """
        from ..db.models import PlatformConfig

        try:
            result = await db.execute(select(PlatformConfig))
            rows = result.scalars().all()
        except Exception as e:
            logger.warning("Could not load platform_config: %s", e)
            self._loaded = True
            return

        for row in rows:
            try:
                parsed = json.loads(row.value)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid JSON in platform_config key=%s", row.key)
                continue
            if row.key in self._SECTIONS:
                setattr(self, self._SECTIONS[row.key][1], parsed)

        self._loaded = True
        logger.info(
            "Platform defaults loaded: %d features, %d quotas, "
            "%d spending, %d retention overrides",
            len(self._db_features), len(self._db_quotas),
            len(self._db_spending), len(self._db_retention),
        )

    def _get_effective(self, section: str) -> dict[str, Any]:
        """Get effective values for a section (hardcoded + DB overrides)."""
        defaults, attr = self._SECTIONS[section]
        db_overrides = getattr(self, attr)
        if not defaults:
            # Sections without hardcoded defaults (e.g. retention)
            # return raw DB overrides.
            return dict(db_overrides)
        effective = dict(defaults)
        for k, v in db_overrides.items():
            if v is not None and k in effective:
                effective[k] = v
        return effective

    def get_platform_features(self) -> dict[str, Any]:
        """Get effective platform features (hardcoded + DB overrides)."""
        return self._get_effective("features")

    def get_platform_quotas(self) -> dict[str, Any]:
        """Get effective platform quotas (hardcoded + DB overrides)."""
        return self._get_effective("quotas")

    def get_platform_spending(self) -> dict[str, Any]:
        """Get effective platform spending limits (hardcoded + DB overrides)."""
        return self._get_effective("spending")

    def get_platform_retention(self) -> dict[str, Any]:
        """Get effective retention config (defaults + DB overrides)."""
        return self._get_effective("retention")

    async def update_platform_defaults(
        self,
        db: AsyncSession,
        section: str,
        values: dict[str, Any],
        updated_by: Optional[str] = None,
    ) -> dict[str, Any]:
        """Update platform defaults in the DB and refresh in-memory cache.

        Args:
            db: Database session.
            section: One of "features", "quotas", "spending", "retention".
            values: Key-value pairs to update (null values reset to hardcoded).
            updated_by: User ID of the admin making the change.

        Returns:
            The effective values after the update.
        """
        if section not in self._SECTIONS:
            raise ValueError(f"Invalid section: {section}")

        from ..db.models import PlatformConfig
        from datetime import datetime, timezone

        _, attr = self._SECTIONS[section]
        current = dict(getattr(self, attr))

        # Apply updates: null values remove the override
        for k, v in values.items():
            if v is None:
                current.pop(k, None)
            else:
                current[k] = v

        # Upsert into DB
        result = await db.execute(
            select(PlatformConfig).where(PlatformConfig.key == section)
        )
        row = result.scalar_one_or_none()

        if row is None:
            row = PlatformConfig(
                key=section,
                value=json.dumps(current),
                updated_at=datetime.now(timezone.utc),
                updated_by=updated_by,
            )
            db.add(row)
        else:
            row.value = json.dumps(current)
            row.updated_at = datetime.now(timezone.utc)
            row.updated_by = updated_by

        await db.commit()

        # Refresh in-memory cache
        setattr(self, attr, current)

        return self._get_effective(section)

    def resolve_features(self, reseller_features_json: Optional[str],
                         user_features_json: Optional[str]) -> dict[str, Any]:
        """Resolve effective features: platform -> reseller -> user.
        Null values = inherit from parent level.
        """
        effective = self.get_platform_features()

        # Layer 2: Reseller overrides, Layer 3: User overrides
        self._apply_json_overrides(effective, reseller_features_json, "reseller")
        self._apply_json_overrides(effective, user_features_json, "user")

        return effective

    @staticmethod
    def _apply_json_overrides(effective: dict, json_str: Optional[str],
                              label: str) -> None:
        """Apply JSON overrides to an effective feature dict."""
        if not json_str:
            return
        try:
            overrides = json.loads(json_str)
            for k, v in overrides.items():
                if v is not None and k in effective:
                    effective[k] = v
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid %s features JSON", label)

    def validate_override(self, key: str, value: Any,
                          ceiling: dict[str, Any]) -> tuple[bool, str]:
        """Validate that an override doesn't exceed its ceiling.
        For numeric values: user value <= ceiling value.
        For lists: user list is subset of ceiling list.
        For booleans: user can only disable (True->False ok, False->True not ok if ceiling is False).
        Returns (valid, error_message).
        """
        if key not in ceiling:
            return (False, f"Unknown feature: {key}")

        ceil_val = ceiling[key]

        if isinstance(ceil_val, bool):
            # Can only restrict: if ceiling is False, override cannot be True
            if not ceil_val and value is True:
                return (False, f"Cannot enable {key}: restricted by parent")
            return (True, "")

        if isinstance(ceil_val, (int, float)):
            if isinstance(value, (int, float)) and value > ceil_val:
                return (False, f"{key} cannot exceed {ceil_val}")
            return (True, "")

        if isinstance(ceil_val, list) and isinstance(value, list):
            extras = set(value) - set(ceil_val)
            if extras:
                return (False, f"{key} contains values not in ceiling: {extras}")
            return (True, "")

        return (True, "")

    def get_user_effective_features(self, user: Any, reseller: Any = None) -> dict[str, Any]:
        """Get effective features for a user given their reseller context.
        user and reseller are ORM objects.
        """
        reseller_json = reseller.features_json if reseller else None
        user_json = user.features_json if hasattr(user, 'features_json') else None
        return self.resolve_features(reseller_json, user_json)


feature_flag_service = FeatureFlagService()
