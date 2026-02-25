"""
Pytest configuration for core-tests.

This module contains fixtures and configuration for the agent core tests.
"""
import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "unit: marks tests as unit tests (fast, no external dependencies)"
    )
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (may require external services)"
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add custom command line options for compatibility with run.sh --core."""
    try:
        parser.addoption(
            "--run-e2e",
            action="store_true",
            default=False,
            help="Accepted for compatibility; core-tests have no E2E tests.",
        )
    except ValueError:
        pass  # Already registered by another conftest (e.g., when run from tests/ root)
