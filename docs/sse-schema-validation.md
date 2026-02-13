# SSE Schema Validation

Anthropic's SSE streaming format is used in two contexts:
1. **Direct API calls** — `TraceProcessor` parses events from Claude Agent SDK
2. **LLM Proxy** — Translator produces Anthropic-format events from OpenAI responses

When Anthropic changes the SSE format (new event types, new fields, changed structure), both contexts break. Schema validation tests detect these changes early.

---

## Files

- `src/api/llm_proxy/schemas.py` — Pydantic models for all SSE event types (shared by both contexts)
- `tests/backend/test_sse_schemas.py` — 59 tests validating schemas
- `tests/backend/fixtures/anthropic_sse_samples.json` — Recorded real API events
- `scripts/record_sse_samples.py` — Re-records fixtures from live API

---

## What Breaks When Anthropic Changes Format

| Component | Location | Impact |
|-----------|----------|--------|
| TraceProcessor | `src/core/trace_processor.py` | Fails to parse new event types, misses usage stats, wrong status |
| LLM Proxy Translator | `src/api/llm_proxy/translator.py` | Produces invalid events, SDK rejects responses |
| Event Persistence | `src/services/event_service.py` | New fields not stored, lost in polling fallback |

---

## Test Categories and What Failures Indicate

| Test Class | Failure Indicates |
|------------|-------------------|
| `TestEnums` | New/renamed stop reasons, content types, or delta types |
| `TestContentBlocks` | Changed structure of text/tool_use/thinking blocks |
| `TestDeltas` | Changed structure of text_delta/input_json_delta/thinking_delta |
| `TestUsage` | New usage fields (tokens, caching, service tier) |
| `TestSSEEvents` | Changed event payload structure |
| `TestSSEParsing` | Changed SSE wire format (event:/data: lines) |
| `TestSSEStreamValidation` | Changed event ordering requirements |
| `TestToolUseStreamOrder` | Changed tool input streaming protocol |
| `TestTranslatorOutput` | Our translator produces invalid events |
| `TestRecordedAPIEvents` | Real API format differs from schemas |
| `TestTraceProcessorEventCoverage` | TraceProcessor missing handler for new event/delta type |

---

## Workflow When Claude Code Updates and Tests Fail

```bash
# 1. Run tests to see what broke
./run.sh test --subset "sse_schemas"

# 2. Re-record fixtures from live API
python3 scripts/record_sse_samples.py

# 3. Run tests again — new failures show schema drift
./run.sh test --subset "sse_schemas"

# 4. Update schemas.py to match new API format
# 5. Update translator.py if LLM proxy output format changed
# 6. Update trace_processor.py if new event types need handling
# 7. Run tests until green
```

---

## Recording Script Usage

```bash
# Record from API (uses ANTHROPIC_API_KEY from env or secrets.yaml)
python3 scripts/record_sse_samples.py

# Preview only (no API calls)
python3 scripts/record_sse_samples.py --dry-run

# Use specific model
python3 scripts/record_sse_samples.py --model claude-sonnet-4-20250514
```

The script makes 3 API calls: text-only, single tool call, multiple tool calls. Output saved to `tests/backend/fixtures/anthropic_sse_samples.json`.

---

## Known API Fields (Discovered via Recording)

- `ping` event: keepalive during long streams
- `cache_creation`: nested object with `ephemeral_5m_input_tokens`, `ephemeral_1h_input_tokens`
- `service_tier`, `inference_geo`: in usage object
- `input_json_delta`: first delta can be empty string
