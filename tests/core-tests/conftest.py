"""
Pytest configuration for core-tests.

This module contains fixtures and configuration for the agent core tests.
"""
import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (may require external services)"
    )



