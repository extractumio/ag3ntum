# Redis SSE Test Suite

Comprehensive test suite for Redis-based SSE event streaming implementation.

## Test Coverage

### 1. Unit Tests (`test_redis_event_hub.py`)
Tests for RedisEventHub core functionality:
- ✅ Subscribe/unsubscribe operations
- ✅ Publish to Redis channels (session:{id}:events pattern)
- ✅ Background listener tasks
- ✅ Backpressure handling (drop oldest when queue full)
- ✅ Statistics tracking (events received/dropped)
- ✅ Multiple subscribers (fanout)
- ✅ Error handling (invalid JSON, nonexistent sessions)
- ✅ Connection pooling and cleanup

**Test Classes:**
- `TestRedisEventHubBasics` - Subscribe, unsubscribe, channel naming
- `TestRedisEventHubPublish` - Publishing events, fanout
- `TestRedisEventHubBackpressure` - Queue overflow handling
- `TestRedisEventHubStats` - Statistics tracking
- `TestRedisEventHubErrorHandling` - Error scenarios

### 2. Integration Tests (`test_redis_integration.py`)
Tests for Redis + SQLite persistence:
- ✅ Publish to Redis FIRST, then persist to SQLite (reversed order)
- ✅ Partial events (Redis only, not persisted)
- ✅ Final events (Redis + SQLite)
- ✅ Multiple events in sequence
- ✅ Redis failure still persists to SQLite
- ✅ EventSinkQueue adapter
- ✅ EventingTracer integration
- ✅ Late subscriber demonstrates need for replay

**Test Classes:**
- `TestRedisSQLiteIntegration` - Redis-first order verification
- `TestEventSinkQueue` - EventSinkQueue adapter
- `TestEventDeliveryTiming` - Race conditions and replay needs

### 3. E2E Tests (`test_redis_sse_e2e.py`)
Tests for SSE streaming with overlap buffer:
- ✅ 10-event overlap buffer catches late events
- ✅ Deduplication by sequence number
- ✅ Overlap + deduplication together
- ✅ Terminal events (agent_complete, error, cancelled)
- ✅ Late subscriber replay from DB
- ✅ Multiple concurrent SSE connections
- ✅ Subscriber disconnect doesn't affect others
- ✅ Heartbeat on timeout

**Test Classes:**
- `TestSSEOverlapBuffer` - Overlap buffer strategy
- `TestSSETerminalEvents` - Terminal event handling
- `TestSSELateSubscriber` - Late subscriber replay
- `TestSSEConcurrentSubscribers` - Multiple connections
- `TestSSEHeartbeat` - Heartbeat mechanism

### 4. Feature Flag Tests (`test_redis_feature_flag.py`)
Tests for redis_sse feature flag:
- ✅ `redis_sse: false` uses in-memory EventHub
- ✅ `redis_sse: true` uses RedisEventHub
- ✅ Missing api.yaml defaults to in-memory
- ✅ Redis connection validation (fails fast if unavailable)
- ✅ In-memory hub (single process only)
- ✅ Redis hub (cross-container support)
- ✅ Configuration loading from api.yaml

**Test Classes:**
- `TestFeatureFlagToggle` - Feature flag behavior
- `TestFeatureFlagBehavior` - Implementation differences
- `TestFeatureFlagConfiguration` - Config loading
- `TestFeatureFlagDocumentation` - Default behaviors

---

## Prerequisites

### 1. Redis Running
Redis must be running for tests to pass. The test suite uses **Redis DB 1** (not DB 0) to avoid conflicts.

**Check Redis:**
```bash
docker ps | grep redis
# Should see: project-redis-1 running
```

**Start Redis if needed:**
```bash
./run.sh build
```

### 2. Install Test Dependencies
```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio
```

---

## Running Tests

### Run All Redis Tests
```bash
# From project root
./run.sh test tests/backend/redis/
```

### Run Specific Test Files
```bash
# Unit tests only
./run.sh test tests/backend/redis/test_redis_event_hub.py

# Integration tests only
./run.sh test tests/backend/redis/test_redis_integration.py

# E2E tests only
./run.sh test tests/backend/redis/test_redis_sse_e2e.py

# Feature flag tests only
./run.sh test tests/backend/redis/test_redis_feature_flag.py
```

### Run Specific Test Classes
```bash
# Test backpressure handling
./run.sh test tests/backend/redis/test_redis_event_hub.py::TestRedisEventHubBackpressure

# Test overlap buffer
./run.sh test tests/backend/redis/test_redis_sse_e2e.py::TestSSEOverlapBuffer

# Test feature flag
./run.sh test tests/backend/redis/test_redis_feature_flag.py::TestFeatureFlagToggle
```

### Run Specific Test Methods
```bash
# Single test
./run.sh test tests/backend/redis/test_redis_event_hub.py::TestRedisEventHubPublish::test_publish_event

# With verbose output
./run.sh test tests/backend/redis/test_redis_event_hub.py::TestRedisEventHubPublish::test_publish_event -v
```

### Run Tests with Markers
```bash
# Run only tests marked with @pytest.mark.redis
./run.sh test tests/backend/redis/ -m redis
```

---

## Test Markers

Tests use pytest markers to categorize:
- `@pytest.mark.redis` - Requires Redis to be running (fails fast if unavailable)
- `@pytest.mark.asyncio` - Async test using pytest-asyncio

**Note:** All tests in this suite require Redis. Tests will fail immediately with clear error messages if Redis is not running or misconfigured.

---

## What Each Test Verifies

### Unit Tests: RedisEventHub
**Purpose:** Verify RedisEventHub works correctly in isolation.

**Key Scenarios:**
1. **Subscribe** creates local queue + background listener task
2. **Unsubscribe** cancels listener task and cleans up
3. **Publish** sends events to Redis channel `session:{id}:events`
4. **Fanout** delivers events to multiple subscribers
5. **Backpressure** drops oldest event when queue full (500 max)
6. **Stats** tracks events received/dropped per subscriber

**Example:**
```python
# Subscribe
queue = await redis_event_hub.subscribe("session_123")

# Publish
await redis_event_hub.publish("session_123", {
    "type": "test_event",
    "sequence": 1,
    "data": {}
})

# Receive
event = await queue.get()  # Gets event from Redis
```

### Integration Tests: Redis + SQLite
**Purpose:** Verify events flow correctly: Redis → SQLite.

**Key Scenarios:**
1. **Order:** Events published to Redis FIRST (~1ms), then persisted to SQLite (~50ms)
2. **Partial events:** Published to Redis, NOT persisted to SQLite
3. **Final events:** Published to Redis AND persisted to SQLite
4. **Resilience:** Redis failure still persists to SQLite
5. **Late subscribers:** Need SQLite replay (Redis is ephemeral)

**Example:**
```python
# Create tracer with Redis + SQLite sink
tracer = EventingTracer(
    NullTracer(),
    event_queue=EventSinkQueue(redis_hub, session_id),
    event_sink=mock_sqlite_sink,  # Simulates SQLite
    session_id=session_id
)

# Emit event
tracer.emit_event("test", {"msg": "hello"}, persist_event=True)

# Redis gets it first (fast)
redis_event = await redis_queue.get()

# SQLite sink also called (slower, but durable)
assert mock_sqlite_sink.called
```

### E2E Tests: SSE Streaming
**Purpose:** Verify SSE endpoint logic with overlap buffer.

**Key Scenarios:**
1. **Overlap buffer:** Replays from `sequence - 10` instead of `sequence`
2. **Deduplication:** Prevents duplicates using `seen_sequences` set
3. **Race condition:** Catches events published to Redis but not yet in SQLite
4. **Terminal events:** Closes SSE on `agent_complete`, `error`, `cancelled`
5. **Late subscribers:** Replay from SQLite + live from Redis
6. **Multiple connections:** All subscribers receive events

**Example:**
```python
# SSE replay logic with overlap buffer
last_sequence = 100
replay_start_sequence = max(0, last_sequence - 10)  # 90

# Replay from SQLite (sequences 91-100)
db_events = await event_service.list_events(after_sequence=replay_start_sequence)

# Deduplicate
seen_sequences = set()
for event in db_events:
    if event["sequence"] in seen_sequences:
        continue  # Skip duplicate
    seen_sequences.add(event["sequence"])
    # Yield to SSE

# Stream live from Redis
while True:
    event = await redis_queue.get()
    if event["sequence"] in seen_sequences:
        continue  # Skip duplicate
    seen_sequences.add(event["sequence"])
    # Yield to SSE
```

### Feature Flag Tests
**Purpose:** Verify feature flag toggles between implementations.

**Key Scenarios:**
1. **`redis_sse: false`** → Uses in-memory EventHub
2. **`redis_sse: true`** → Uses RedisEventHub
3. **Missing config** → Defaults to in-memory
4. **Redis validation** → Ensures connection is valid (fails fast if not)
5. **Cross-container** → RedisEventHub works, in-memory doesn't

**Example:**
```yaml
# config/api.yaml
redis:
  url: "redis://redis:6379/0"  # Required - Redis URL for SSE streaming
```

---

## Test Fixtures

### `redis_url` (session scope)
Returns Redis connection URL for tests (uses DB 1, not DB 0).

### `redis_connection` (function scope)
Provides Redis connection for tests. Fails fast if Redis is not reachable with clear error message.

### `redis_event_hub` (function scope)
Provides RedisEventHub instance for tests. Fails fast if Redis is not available or misconfigured.

### `clean_redis` (function scope)
Provides Redis connection with DB flushed before and after test.

### `test_session_id` (function scope)
Generates unique test session ID (e.g., `test_session_a1b2c3d4`).

### `mock_event_sink` (function scope)
Mock async function simulating SQLite persistence.

---

## Common Test Patterns

### Testing Redis Pub/Sub
```python
@pytest.mark.redis
@pytest.mark.asyncio
async def test_publish_subscribe(redis_event_hub, test_session_id):
    # Subscribe
    queue = await redis_event_hub.subscribe(test_session_id)
    await asyncio.sleep(0.1)  # Let listener start

    # Publish
    event = {"type": "test", "sequence": 1, "session_id": test_session_id, "data": {}}
    await redis_event_hub.publish(test_session_id, event)

    # Receive
    received = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert received["type"] == "test"

    # Cleanup
    await redis_event_hub.unsubscribe(test_session_id, queue)
```

### Testing Redis + SQLite Integration
```python
@pytest.mark.redis
@pytest.mark.asyncio
async def test_redis_then_sqlite(redis_event_hub, test_session_id, mock_event_sink):
    # Create tracer
    event_queue = EventSinkQueue(redis_event_hub, test_session_id)
    tracer = EventingTracer(
        NullTracer(),
        event_queue=event_queue,
        event_sink=mock_event_sink,
        session_id=test_session_id
    )

    # Subscribe to Redis
    queue = await redis_event_hub.subscribe(test_session_id)
    await asyncio.sleep(0.1)

    # Emit event
    tracer.emit_event("test", {"msg": "hello"}, persist_event=True)
    await asyncio.sleep(0.5)

    # Verify Redis received first
    redis_event = await queue.get()
    assert redis_event["type"] == "test"

    # Verify SQLite sink called
    assert mock_event_sink.called

    await redis_event_hub.unsubscribe(test_session_id, queue)
```

### Testing Feature Flag
```python
def test_feature_flag(tmp_path):
    # Create api.yaml
    api_config = {"features": {"redis_sse": False}}
    api_yaml_path = tmp_path / "api.yaml"
    with open(api_yaml_path, "w") as f:
        yaml.dump(api_config, f)

    # Patch CONFIG_DIR
    with patch("src.services.agent_runner.CONFIG_DIR", tmp_path):
        runner = AgentRunner()
        assert isinstance(runner._event_hub, EventHub)
```

---

## Troubleshooting

### Tests Fail with "Redis connection failed"
**Cause:** Redis is not running or not reachable.

**Error message:** `RuntimeError: Redis connection failed. Ensure Redis is running with './run.sh build'.`

**Solution:**
```bash
# Check Redis
docker ps | grep redis

# Start containers
./run.sh build

# Test connection
docker exec -it project-ag3ntum-api-1 python -c "import redis; r=redis.Redis(host='redis', port=6379); print(r.ping())"
```

### Tests Fail with ImportError
**Cause:** redis package not installed.

**Error message:** `ModuleNotFoundError: No module named 'redis'`

**Solution:**
```bash
./run.sh build --no-cache
```

### Tests Timeout
**Cause:** Redis listener tasks not starting or events not being received.

**Solution:**
- Increase `await asyncio.sleep()` delays in tests
- Check Redis logs: `docker logs project-redis-1`
- Verify Redis channel names: `docker exec -it project-redis-1 redis-cli PUBSUB CHANNELS`

### Tests Fail Intermittently
**Cause:** Race conditions in async tests.

**Solution:**
- Add `await asyncio.sleep(0.1)` after subscribe to let listener start
- Add `await asyncio.sleep(0.5)` after publish to let events propagate
- Increase timeouts in `asyncio.wait_for()`

---

## Test Statistics

**Total Tests:** ~50+ tests
**Test Files:** 4 files
**Lines of Code:** ~1,500 lines
**Coverage Areas:**
- Unit tests: 15+ tests
- Integration tests: 10+ tests
- E2E tests: 15+ tests
- Feature flag tests: 10+ tests

**Markers:**
- `@pytest.mark.redis`: ~45 tests
- `@pytest.mark.asyncio`: ~45 tests

---

## Next Steps

After tests pass:

1. **Enable Redis SSE:**
   ```yaml
   # config/api.yaml
   features:
     redis_sse: true
   ```

2. **Restart containers:**
   ```bash
   ./run.sh restart
   ```

3. **Monitor logs:**
   ```bash
   docker logs -f project-ag3ntum-api-1 | grep -i redis
   # Should see: "Using RedisEventHub for SSE streaming"
   ```

4. **Test manually:**
   - Create session via UI
   - Verify SSE events stream correctly
   - Check Redis: `docker exec -it project-redis-1 redis-cli MONITOR`

5. **Run load tests:**
   - 100+ concurrent SSE connections
   - Verify no event loss
   - Check Redis memory: `docker exec -it project-redis-1 redis-cli INFO memory`

---

## References

- **Implementation:** `src/services/redis_event_hub.py`
- **Integration:** `src/services/agent_runner.py`
- **SSE Endpoint:** `src/api/routes/sessions.py`
- **Design Doc:** `docs/redis_sse_transition_plan.md`
- **Summary:** `docs/redis_implementation_summary.md`
