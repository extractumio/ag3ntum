"""
Redis SSE implementation tests.

Tests cover:
- RedisEventHub unit tests (pub/sub, backpressure, stats)
- Integration tests (Redis + SQLite persistence)
- E2E SSE streaming tests (overlap buffer, deduplication)
- Feature flag toggling tests
"""
