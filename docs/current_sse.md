# SSE (Server-Sent Events) Implementation

This document describes the current SSE implementation for real-time event streaming between the Ag3ntum backend and the web frontend.

## Overview

Ag3ntum uses Server-Sent Events (SSE) to stream real-time execution events from the backend to the frontend during agent task execution. This enables live updates of tool calls, messages, errors, and completion status without polling.

```
┌─────────────┐         POST /sessions/run          ┌─────────────┐
│   Frontend  │────────────────────────────────────▶│   Backend   │
│   (React)   │                                     │  (FastAPI)  │
│             │◀────────────────────────────────────│             │
│             │   { session_id, status: "running" } │             │
│             │                                     │             │
│             │    GET /sessions/{id}/events        │             │
│             │────────────────────────────────────▶│             │
│             │                                     │             │
│             │◀═══════════════════════════════════ │             │
│             │         SSE Event Stream            │             │
│             │   (agent_start, tool_*, message,    │             │
│             │    agent_complete, error, etc.)     │             │
└─────────────┘                                     └─────────────┘
```

---

## Session and Task Lifecycle

### 1. Task Initiation

**New Session Flow:**
1. User enters task in the frontend input field
2. Frontend calls `POST /api/v1/sessions/run` with task and config
3. Backend creates a session record in the database (status: `pending`)
4. Backend creates file-based session folder (via `SessionManager`)
5. Backend persists `user_message` event for replay
6. Backend starts the agent in a background asyncio task via `agent_runner.start_task()`
7. Backend updates session status to `running`
8. Backend returns `{ session_id, status: "running" }`
9. Frontend immediately opens an SSE connection to `GET /sessions/{id}/events`

**Session Continuation Flow:**
1. User enters follow-up task while a previous session exists
2. Frontend calls `POST /api/v1/sessions/{id}/task` with new task
3. Backend checks session status:
   - If `cancelled` and not resumable → returns HTTP 400
   - If resumable → prepends resume context to task
4. Backend starts agent with `resume_session_id` pointing to itself
5. Frontend opens SSE connection to stream new events

### 2. SSE Connection Establishment

The frontend uses the native browser `EventSource` API:

```typescript
const url = `${baseUrl}/api/v1/sessions/${sessionId}/events?token=${token}`;
const source = new EventSource(url);
```

**Authentication:** Token is passed via query parameter (EventSource limitation - cannot set headers).

**Backend Endpoint:** `GET /sessions/{id}/events`
- Validates token and session ownership
- Subscribes to EventHub for live events
- Replays missed events from database
- Returns `StreamingResponse` with `text/event-stream` media type

### 3. Event Processing Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Event Processing Pipeline                           │
│                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │                         Claude Agent SDK                                 │ │
│  │                                                                          │ │
│  │  ClaudeSDKClient.receive_response()                                     │ │
│  │       ↓ yields SystemMessage, AssistantMessage, UserMessage, etc.       │ │
│  └──────────────────────────────┬───────────────────────────────────────────┘ │
│                                 ↓                                             │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │                        TraceProcessor                                    │ │
│  │  (src/core/trace_processor.py)                                           │ │
│  │                                                                          │ │
│  │  process_message(message: SDKMessage)                                   │ │
│  │    ├── SystemMessage.init → tracer.on_agent_start()                     │ │
│  │    ├── AssistantMessage                                                 │ │
│  │    │     ├── TextBlock → tracer.on_message()                            │ │
│  │    │     ├── ThinkingBlock → tracer.on_thinking()                       │ │
│  │    │     └── ToolUseBlock → tracer.on_tool_start()                      │ │
│  │    ├── UserMessage.ToolResultBlock → tracer.on_tool_complete()          │ │
│  │    ├── StreamEvent → tracer.on_message(is_partial=True)                 │ │
│  │    └── ResultMessage → tracer.on_agent_complete()                       │ │
│  └──────────────────────────────┬───────────────────────────────────────────┘ │
│                                 ↓                                             │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │                       EventingTracer (Wrapper)                           │ │
│  │  (src/core/tracer.py lines 2420-2991)                                    │ │
│  │                                                                          │ │
│  │  Wraps BackendConsoleTracer and:                                        │ │
│  │  1. Calls wrapped tracer method (for logging)                           │ │
│  │  2. Constructs structured event with sequence number                    │ │
│  │  3. For streaming messages:                                              │ │
│  │     - Buffers partial text until complete                               │ │
│  │     - Extracts YAML frontmatter (structured output)                     │ │
│  │     - Emits partial events (persist_event=False)                        │ │
│  │     - Emits final event with full_text (persist_event=True)             │ │
│  │  4. Calls persist_then_publish() async task                             │ │
│  └──────────────────────────────┬───────────────────────────────────────────┘ │
│                                 ↓                                             │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │                      persist_then_publish()                              │ │
│  │                                                                          │ │
│  │  async def persist_then_publish():                                      │ │
│  │      if persist_event:                                                   │ │
│  │          await event_sink(event)  # Write to SQLite                     │ │
│  │      await event_queue.put(event)  # Publish to EventHub                │ │
│  │                                                                          │ │
│  │  CRITICAL: DB write completes BEFORE EventHub publish                   │ │
│  │  This prevents race conditions where SSE misses events                  │ │
│  └───────────┬──────────────────────────────────┬────────────────────────────┘ │
│              ↓                                  ↓                             │
│  ┌─────────────────────────┐     ┌────────────────────────────────────────┐  │
│  │     EventService        │     │           EventSinkQueue               │  │
│  │ (event_service.py)      │     │         (event_stream.py)              │  │
│  │                         │     │                                        │  │
│  │ record_event() →        │     │ put_nowait() → EventHub.publish()     │  │
│  │   _persist_event() →    │     │                                        │  │
│  │   INSERT INTO events    │     └───────────────────┬────────────────────┘  │
│  │                         │                         ↓                       │
│  │ Features:               │     ┌────────────────────────────────────────┐  │
│  │ - Retry logic (3x)      │     │              EventHub                  │  │
│  │ - 10s timeout           │     │         (event_stream.py)              │  │
│  │ - Skip partial messages │     │                                        │  │
│  │ - Extract full_text     │     │ Pub/sub fanout per session:           │  │
│  │ - Update resume_id      │     │ - subscribers: Dict[session, Set[Q]]  │  │
│  │                         │     │ - max_queue_size: 500                  │  │
│  └─────────────────────────┘     │ - Backpressure: drop oldest           │  │
│                                  │ - Stats tracking per subscriber        │  │
│                                  │                                        │  │
│                                  │  publish(session_id, event):          │  │
│                                  │    for queue in subscribers[session]: │  │
│                                  │      if queue.full():                  │  │
│                                  │        queue.get_nowait()  # drop     │  │
│                                  │        stats.events_dropped += 1       │  │
│                                  │      queue.put_nowait(event)           │  │
│                                  └───────────────────┬────────────────────┘  │
│                                                      ↓                       │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │                    SSE Endpoint Generator                                │ │
│  │  (src/api/routes/sessions.py:stream_events)                              │ │
│  │                                                                          │ │
│  │  async def event_generator():                                           │ │
│  │    # 1. Subscribe to EventHub FIRST                                     │ │
│  │    queue = await agent_runner.subscribe(session_id)                     │ │
│  │                                                                          │ │
│  │    # 2. Replay missed events from DB                                    │ │
│  │    replay_events = await event_service.list_events(after_sequence)      │ │
│  │    for event in replay_events:                                          │ │
│  │      yield f"id: {sequence}\ndata: {json}\n\n"                          │ │
│  │      if event.type in terminal_events: return                           │ │
│  │                                                                          │ │
│  │    # 3. Stream live events from queue                                   │ │
│  │    while True:                                                           │ │
│  │      event = await asyncio.wait_for(queue.get(), timeout=30)            │ │
│  │      if sequence <= last_sequence: continue  # Skip replayed           │ │
│  │      yield f"id: {sequence}\ndata: {json}\n\n"                          │ │
│  │      if event.type in terminal_events: break                            │ │
│  │                                                                          │ │
│  │    # 4. Cleanup                                                          │ │
│  │    await agent_runner.unsubscribe(session_id, queue)                    │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Key Components:**

1. **TraceProcessor** (`trace_processor.py`): Bridges SDK message types to tracer interface
   - Handles SystemMessage (init, errors), AssistantMessage (text, thinking, tool_use), UserMessage (tool_result)
   - Tracks pending tool calls to match tool_start with tool_complete
   - Tracks subagent (Task tool) invocations for nested execution

2. **EventingTracer** (`tracer.py:2420`): Wrapper that adds event emission
   - Wraps BackendConsoleTracer for logging
   - Buffers streaming text to extract structured output headers
   - Emits partial events without persistence, final events with persistence
   - Uses `persist_then_publish()` pattern for race-condition-free delivery

3. **EventService** (`event_service.py`): Database persistence with robustness
   - Retry logic (3 attempts with exponential backoff)
   - 10-second timeout on DB operations
   - Skips partial messages to reduce writes
   - Updates resume_id on agent_start events

4. **EventHub** (`event_stream.py`): Pub/sub fanout
   - Per-session subscriber management
   - Bounded queues (500 events max)
   - Backpressure handling via dropping oldest events
   - Statistics tracking per subscriber

5. **SSE Generator** (`sessions.py:stream_events`): HTTP streaming
   - Subscribe-then-replay pattern for guaranteed delivery
   - Heartbeats every 30 seconds
   - Sequence-based deduplication

### Event Persistence Strategy

| Event Type | Streamed to Frontend | Persisted to DB |
|------------|---------------------|-----------------|
| Partial `message` (`is_partial: true`) | ✅ Yes | ❌ No |
| Final `message` (`is_partial: false`) | ✅ Yes (with `full_text`) | ✅ Yes |
| `tool_start`, `tool_complete` | ✅ Yes | ✅ Yes |
| `agent_start`, `agent_complete` | ✅ Yes | ✅ Yes |
| `error`, `cancelled` | ✅ Yes | ✅ Yes |
| `subagent_start`, `subagent_stop` | ✅ Yes | ✅ Yes |
| Partial `subagent_message` (`is_partial: true`) | ✅ Yes | ❌ No |
| Final `subagent_message` (`is_partial: false`) | ✅ Yes | ✅ Yes |
| `thinking` | ✅ Yes | ✅ Yes |
| `profile_switch`, `hook_triggered` | ✅ Yes | ✅ Yes |
| `conversation_turn` | ✅ Yes | ✅ Yes |
| `metrics_update` | ✅ Yes | ✅ Yes |

**Algorithm Notes:**
- Streaming events are emitted as partial chunks to the UI for real-time feedback
- Only **final** messages (with `full_text` field) are persisted for replay
- The `EventingTracer` buffers streaming text and extracts structured output headers before emitting

### 4. Streaming Message Processing

The `EventingTracer` handles streaming text with structured output extraction:

```python
# Streaming message state machine in EventingTracer
class EventingTracer:
    _stream_header_buffer: str = ""         # Buffer for YAML frontmatter detection
    _stream_header_expected: Optional[bool] # None=detecting, True=in header, False=body only
    _stream_header_wrapped: bool = False    # True if wrapped in ``` fences
    _stream_structured_fields: Optional[dict] # Extracted frontmatter fields
    _stream_full_text: str = ""             # Accumulated body text
    _stream_active: bool = False            # True during streaming

    def on_message(self, text: str, is_partial: bool = False):
        if is_partial:
            # Detect/extract YAML frontmatter (---\nkey: value\n---)
            body_text = self._consume_stream_text(text)
            self._stream_full_text += body_text
            self.emit_event("message", {
                "text": body_text,
                "is_partial": True,
            }, persist_event=False)  # Don't persist partials
            return

        # Final message - emit with full_text
        self.emit_event("message", {
            "text": body_text,
            "full_text": self._stream_full_text,  # Complete accumulated text
            "is_partial": False,
            "structured_fields": self._stream_structured_fields,
            "structured_status": self._stream_structured_status,
        })  # Persisted by default
```

**Frontmatter Detection:**
The tracer detects YAML frontmatter in agent responses:
```yaml
---
status: COMPLETE
error: null
---
The actual message body here...
```

This is extracted into `structured_fields`, `structured_status`, `structured_error` for the UI to display.

### 5. Task Termination

A task terminates when:

1. **Successful Completion:** Agent emits `agent_complete` event with status
2. **Error:** Agent emits `error` event
3. **Cancellation:** User cancels via `POST /sessions/{id}/cancel`, agent emits `cancelled` event
4. **Timeout:** Backend detects idle queue while task is no longer running

---

## Event Types

### Core Event Types

| Event Type | When Emitted | Key Data Fields |
|------------|--------------|-----------------|
| `agent_start` | Agent begins execution | `session_id`, `model`, `tools`, `skills`, `task` |
| `user_message` | User message recorded | `text` |
| `thinking` | Agent extended thinking | `text` |
| `message` | Agent text response | `text`, `full_text`, `is_partial`, `structured_fields`, `structured_status`, `structured_error` |
| `tool_start` | Before tool execution | `tool_name`, `tool_input`, `tool_id` |
| `tool_complete` | After tool execution | `tool_name`, `tool_id`, `result`, `duration_ms`, `is_error` |
| `agent_complete` | Task finished | `status`, `num_turns`, `duration_ms`, `total_cost_usd`, `usage`, `model`, `cumulative_*` |
| `error` | Error occurred | `message`, `error_type` |
| `cancelled` | Task was cancelled | `message`, `resumable` |

### Subagent Event Types

| Event Type | When Emitted | Key Data Fields |
|------------|--------------|-----------------|
| `subagent_start` | Task tool invokes a subagent | `task_id`, `subagent_name`, `prompt_preview` |
| `subagent_message` | Message from subagent context | `task_id`, `text`, `is_partial` |
| `subagent_stop` | Subagent completes | `task_id`, `result_preview`, `duration_ms`, `is_error` |

### Additional Event Types

| Event Type | When Emitted | Key Data Fields |
|------------|--------------|-----------------|
| `profile_switch` | Permission profile changed | `profile_type`, `profile_name`, `tools`, `allow_rules_count`, `deny_rules_count` |
| `hook_triggered` | Hook executed | `hook_event`, `tool_name`, `decision`, `message` |
| `conversation_turn` | Turn completed | `turn_number`, `prompt_preview`, `response_preview`, `duration_ms`, `tools_used` |
| `session_connect` | Session connected | `session_id` |
| `session_disconnect` | Session disconnected | `session_id`, `total_turns`, `total_duration_ms` |
| `metrics_update` | Metrics changed | Token counts, cost, turns |

### Terminal Events

The following events signal that the SSE stream should close:

- `agent_complete`
- `error`
- `cancelled`

When the frontend receives any of these, it closes the EventSource connection.

---

## Event Structure

Each event follows this JSON structure:

```json
{
  "type": "tool_start",
  "data": {
    "tool_name": "Read",
    "tool_input": { "file_path": "/path/to/file.py" },
    "tool_id": "tool_123"
  },
  "timestamp": "2026-01-05T12:34:56.789Z",
  "sequence": 42,
  "session_id": "20260105_123456_abc123"
}
```

**Fields:**
- `type`: Event type identifier
- `data`: Event-specific payload
- `timestamp`: ISO 8601 UTC timestamp
- `sequence`: Monotonically increasing event sequence number
- `session_id`: Session identifier (added by EventingTracer)

### SSE Wire Format

Events are sent in SSE format:

```
id: 42
data: {"type":"tool_start","data":{...},"timestamp":"...","sequence":42}

```

**Note:** Each event block ends with double newline. The `id` field matches the sequence number.

---

## Backend Implementation Details

### Event Generation Flow

1. **EventingTracer.emit_event()**:
   ```python
   def emit_event(self, event_type: str, data: dict, persist_event: bool = True):
       self._sequence += 1
       event = {
           "type": event_type,
           "data": data,
           "timestamp": datetime.now(timezone.utc).isoformat(),
           "sequence": self._sequence,
           "session_id": self._session_id,
       }

       async def persist_then_publish():
           # Persist BEFORE publish - critical for race-free delivery
           if self._event_sink and persist_event:
               await self._event_sink(event)  # DB write
           await self._event_queue.put(event)  # EventHub publish

       loop.create_task(persist_then_publish())
   ```

2. **SSE Generator** (FastAPI endpoint):
   ```python
   async def event_generator():
       # Subscribe FIRST to catch live events
       queue = await agent_runner.subscribe(session_id)

       try:
           # Replay missed events from DB
           replay_events = await event_service.list_events(
               session_id, after_sequence=last_sequence, limit=2000
           )
           for event in replay_events:
               yield f"id: {event['sequence']}\ndata: {json.dumps(event)}\n\n"
               if event['type'] in terminal_events:
                   return

           # Stream live events
           while True:
               try:
                   event = await asyncio.wait_for(queue.get(), timeout=30.0)
               except asyncio.TimeoutError:
                   yield ": heartbeat\n\n"
                   if not agent_runner.is_running(session_id) and queue.empty():
                       break
                   continue

               if event['sequence'] <= last_sequence:
                   continue  # Skip already-sent events

               yield f"id: {event['sequence']}\ndata: {json.dumps(event)}\n\n"

               if event['type'] in ('agent_complete', 'error', 'cancelled'):
                   break
       finally:
           await agent_runner.unsubscribe(session_id, queue)
   ```

### Heartbeat Mechanism

The backend sends SSE heartbeat comments (`: heartbeat\n\n`) every 30 seconds to:
1. Keep the connection alive through proxies
2. Detect if the task has ended while waiting

### Event Delivery Guarantee

The system guarantees no events are lost:

```
Timeline:
  T1: EventingTracer calls persist_then_publish()
  T2: Event written to SQLite (await event_sink)
  T3: Event published to EventHub (await event_queue.put)

If SSE subscribes at T0 (before T1):
  → Event arrives via EventHub queue

If SSE subscribes at T2.5 (during persist):
  → Event in DB, will be replayed

If SSE subscribes at T4 (after publish):
  → Event in DB, will be replayed
```

**Key insight:** By awaiting DB persistence before EventHub publish, events are either:
1. In the subscriber's queue (subscribed before publish), or
2. In the database (subscribed after publish)

No race condition is possible.

### Error Handling

**Agent Error Flow:**
```
Agent error/timeout/exception
  → tracer.on_error emits error event
  → session status set to failed
  → SSE stream closes with error event
  → UI renders failure banner + error text
```

**SSE Streaming Error:**
If the SSE generator encounters an error:
- Synthesizes an error event with `sequence: 9998`
- Sends to frontend before closing
- Unsubscribes from EventHub in finally block

---

## Frontend Implementation Details

### SSE Client (`sse.ts`)

```typescript
export function connectSSE(
  baseUrl: string,
  sessionId: string,
  token: string,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void,
  onReconnecting?: (attempt: number) => void,
  initialLastEventId?: string | number | null
): () => void
```

**Reconnection Strategy:**
- Max attempts: 5
- Initial delay: 1000ms
- Exponential backoff: delay * 2^(attempt-1)
- Stops reconnecting on terminal events

**Polling Fallback:**
After max reconnection attempts fail:
- Switches to HTTP polling via `/events/history` endpoint
- Poll interval: 4000ms
- Uses same sequence-based resumption

**Connection Lifecycle:**
```typescript
// State tracking
let isClosed = false;
let lastEventId: string | null = initialLastEventId;

source.onmessage = (event) => {
  const parsed = JSON.parse(event.data);
  lastEventId = event.lastEventId || String(parsed.sequence);
  onEvent(parsed);

  // Terminal events close the connection
  if (parsed.type in ['agent_complete', 'error', 'cancelled']) {
    isClosed = true;
    source.close();
  }
};

source.onerror = () => {
  if (isClosed) return;  // Expected close after terminal event
  reconnectAttempts++;
  // ... exponential backoff reconnection
};
```

### URL Building

```typescript
function buildUrl(): string {
  const params = new URLSearchParams({ token });
  if (lastEventId) {
    params.set('after', lastEventId);  // Resume from sequence
  }
  return `${baseUrl}/api/v1/sessions/${sessionId}/events?${params}`;
}
```

---

## Session State Management

### Status Transitions

```
┌─────────┐   POST /run    ┌─────────┐   agent_complete   ┌──────────┐
│ pending │───────────────▶│ running │──────────────────▶│ complete │
└─────────┘                └────┬────┘                   └──────────┘
                               │
                               │ error event
                               ▼
                          ┌─────────┐
                          │ failed  │
                          └─────────┘
                               │
                               │ POST /cancel
                               ▼
                         ┌───────────┐
                         │ cancelled │
                         └───────────┘
```

### Cancellation Flow

```
User UI → POST /sessions/{id}/cancel
  → AgentRunner.cancel_task()
     → task.cancel() raises CancelledError
     → Check events DB for agent_start (determines resumability)
  → Emit cancelled event with resumable flag
  → DB session status = cancelled
  → UI receives cancelled event → shows cancellation state
```

### Cancellation and Resumability

A cancelled session may or may not be resumable depending on when cancellation occurred:

| Timing | `agent_start` received? | Resumable? |
|--------|------------------------|------------|
| Before agent starts | ❌ No | ❌ No |
| During execution | ✅ Yes | ✅ Yes |
| After completion | N/A | N/A (already done) |

**Resumability is determined by** whether the `agent_start` event was recorded, which contains the `session_id` (Claude's resume_id).

**The `cancelled` event includes:**
```json
{
  "type": "cancelled",
  "data": {
    "message": "Task was cancelled",
    "resumable": true
  }
}
```

**Frontend behavior:**
- If `resumable: true`: User can continue with follow-up messages
- If `resumable: false`: Next message starts a fresh session

**Backend resume context:** When resuming a cancelled session, the backend prepends context:

```
[RESUME CONTEXT]
Previous execution was cancelled by user.

Todo state at cancellation:
  ✓ Read file [completed]
  → Process data [in_progress]
  ○ Write output [pending]

Note: Task(s) marked in_progress were interrupted and may be incomplete.
[END RESUME CONTEXT]
```

---

## Session Continuation and Resumption

### Viewing Completed Sessions (History Replay)

When selecting a completed/cancelled session (not running):

1. Frontend fetches session details: `GET /sessions/{id}`
2. Frontend fetches historical events: `GET /sessions/{id}/events/history`
3. UI builds conversation from persisted events
4. Events are displayed chronologically

**No SSE connection is opened** — historical events are fetched once.

**Algorithm Notes:**
- Replay uses only persisted events (final messages), so the UI shows clean history without partial chunks
- If a running session is selected, UI uses SSE to stream new events starting from the last known sequence

### Continuing a Session

When user submits a follow-up message on an existing session:

1. **Check resumability** (for cancelled sessions)
2. Call `POST /sessions/{id}/task` with new message
3. Backend determines `resume_session_id` from session info
4. If cancelled session: backend prepends resume context
5. Open SSE connection for new events

---

## HTTP Response Headers

The SSE endpoint returns these headers:

```http
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache, no-transform
Connection: keep-alive
X-Accel-Buffering: no  # Disable nginx buffering
```

---

## Sequence Diagram: Complete Task Flow

```
Frontend                          Backend                           Agent
   │                                 │                                 │
   │──POST /sessions/run─────────────▶                                 │
   │                                 │──create_session()               │
   │                                 │──record_user_message()          │
   │                                 │──start_task()──────────────────▶│
   │◀──{ session_id, status }────────│                                 │
   │                                 │                                 │
   │──GET /sessions/{id}/events──────▶                                 │
   │                                 │──subscribe(EventHub)            │
   │                                 │──replay_from_db()               │
   │◀══user_message═════════════════││                                 │
   │                                 │                                 │
   │                                 │◀──SDK:SystemMessage.init────────│
   │                                 │──TraceProcessor.process()       │
   │                                 │──EventingTracer.on_agent_start()│
   │                                 │──persist_then_publish()         │
   │◀══agent_start═══════════════════│                                 │
   │                                 │                                 │
   │                                 │◀──SDK:ToolUseBlock──────────────│
   │                                 │──on_tool_start()                │
   │◀══tool_start════════════════════│                                 │
   │                                 │                                 │
   │                                 │◀──SDK:ToolResultBlock───────────│
   │                                 │──on_tool_complete()             │
   │◀══tool_complete═════════════════│                                 │
   │                                 │                                 │
   │                                 │◀──SDK:StreamEvent (partial)─────│
   │                                 │──on_message(is_partial=True)    │
   │◀══message (partial)═════════════│  (NOT persisted)                │
   │                                 │                                 │
   │                                 │◀──SDK:TextBlock (final)─────────│
   │                                 │──on_message(is_partial=False)   │
   │◀══message (final)═══════════════│  (persisted with full_text)     │
   │                                 │                                 │
   │                                 │◀──SDK:ResultMessage─────────────│
   │                                 │──on_agent_complete()            │
   │◀══agent_complete════════════════│                                 │
   │                                 │                                 │
   │──close EventSource──────────────│                                 │
   │                                 │──unsubscribe(EventHub)          │
   │                                 │──update_session(complete)       │
   │                                 │                                 │
```

---

## File References

| Component | File Path |
|-----------|-----------|
| TraceProcessor | `src/core/trace_processor.py` |
| EventingTracer | `src/core/tracer.py` (lines 2420-2991) |
| BackendConsoleTracer | `src/core/tracer.py` |
| TracerBase | `src/core/tracer.py` (lines 143-279) |
| EventHub | `src/services/event_stream.py` |
| EventSinkQueue | `src/services/event_stream.py` |
| EventService | `src/services/event_service.py` |
| AgentRunner | `src/services/agent_runner.py` |
| SSE endpoint | `src/api/routes/sessions.py` (stream_events) |
| History endpoint | `src/api/routes/sessions.py` (list_events) |
| Frontend SSE client | `src/web_terminal_client/src/sse.ts` |
| Frontend types | `src/web_terminal_client/src/types.ts` |

---

## End-to-End Summary

1. **Start**: Session created (DB + file), user_message persisted, agent starts in background, SSE stream subscribes to EventHub then replays from DB
2. **Run**: SDK messages → TraceProcessor → EventingTracer → persist to DB → publish to EventHub → SSE generator yields to frontend
3. **Streaming**: Partial messages streamed immediately (not persisted), final messages persisted with full_text
4. **Cancel**: Agent cancels, cancelled event emitted with resumable flag based on agent_start presence
5. **Resume**: Session uses stored resume_id to continue from same Claude context, resume context prepended
6. **Reload**: UI fetches history from `/events/history`, connects SSE if session is running

---

## Robustness Features

### EventService
- **Retry logic**: 3 attempts with exponential backoff (50ms → 100ms → 200ms)
- **Timeout**: 10 seconds per DB operation
- **Integrity handling**: Duplicate sequences logged but don't fail

### EventHub
- **Bounded queues**: 500 events max per subscriber
- **Backpressure**: Drops oldest event when queue full
- **Stats tracking**: Events received/dropped per subscriber

### Frontend SSE Client
- **Reconnection**: 5 attempts with exponential backoff
- **Polling fallback**: HTTP polling at 4s interval if SSE fails
- **Sequence tracking**: Resumes from last received event
