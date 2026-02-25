"""Structural test configuration."""


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: Unit tests")
