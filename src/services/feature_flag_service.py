"""Feature flag inheritance: platform -> reseller -> user."""
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Platform-level defaults (baseline)
DEFAULT_FEATURES: dict[str, Any] = {
    "ssh_enabled": True,
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


class FeatureFlagService:
    """Three-tier feature flag resolution."""

    def resolve_features(self, reseller_features_json: Optional[str],
                         user_features_json: Optional[str]) -> dict[str, Any]:
        """Resolve effective features: platform -> reseller -> user.
        Null values = inherit from parent level.
        """
        effective = dict(DEFAULT_FEATURES)

        # Layer 2: Reseller overrides
        if reseller_features_json:
            try:
                reseller_features = json.loads(reseller_features_json)
                for k, v in reseller_features.items():
                    if v is not None and k in effective:
                        effective[k] = v
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid reseller features JSON")

        # Layer 3: User overrides (within reseller ceiling)
        if user_features_json:
            try:
                user_features = json.loads(user_features_json)
                for k, v in user_features.items():
                    if v is not None and k in effective:
                        effective[k] = v
            except (json.JSONDecodeError, TypeError):
                logger.warning("Invalid user features JSON")

        return effective

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

        if isinstance(ceil_val, (int, float)) and ceil_val is not None:
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
