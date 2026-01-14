# Inbound WAF Filter - Request Size Limiting

## Overview

Implemented a Web Application Firewall (WAF) filter module that protects the Ag3ntum API from resource exhaustion attacks by enforcing strict size limits on incoming requests.

## Security Controls

### 1. **Text Content Limit: 100,000 Characters**
- Applies to: Task descriptions, prompts, messages, descriptions, comments
- Action: **Truncate** (not reject) - prevents denial of service while preserving functionality
- Fields affected:
  - `task` - Primary task descriptions
  - `prompt`, `message`, `text` - Text content fields
  - `description`, `comments`, `error`, `output` - Metadata fields

### 2. **File Upload Limit: 10MB**
- Applies to: File uploads (when/if implemented)
- Action: **Reject** with HTTP 413 (Request Entity Too Large)
- Prevents storage exhaustion

### 3. **Request Body Limit: 20MB**
- Applies to: Overall HTTP request body size
- Action: **Reject** with HTTP 413 (Request Entity Too Large)
- Allows for large JSON payloads with base64-encoded content

## Implementation

### Files Created

**`src/api/waf_filter.py`** - Core WAF filter module:
- `truncate_text_content()` - Truncates text to 100,000 chars
- `validate_file_size()` - Validates file uploads
- `validate_request_body_size()` - Validates overall request size
- `filter_request_data()` - Recursively filters dict data
- `filter_pydantic_model()` - Filters Pydantic models
- Utility functions for size formatting and info

### Integration Points

**1. Pydantic Model Validators** (`src/api/models.py`):
```python
@field_validator("task")
@classmethod
def truncate_task(cls, v: str) -> str:
    """Apply WAF filter to task field."""
    return truncate_text_content(v, "task") or ""
```

Applied to:
- `RunTaskRequest.task`
- `CreateSessionRequest.task`
- `StartTaskRequest.task`

**2. HTTP Middleware** (`src/api/main.py`):
```python
@app.middleware("http")
async def waf_middleware(request: Request, call_next):
    """WAF filter to validate request body sizes."""
    await validate_request_size(request)
    response = await call_next(request)
    return response
```

## Behavior

### Text Content Truncation

**Before WAF**:
```json
{
  "task": "Very long task description... (200,000 characters)"
}
```

**After WAF**:
```json
{
  "task": "Very long task description... (truncated to 100,000 characters)"
}
```

**Log Output**:
```
WARNING: WAF: Truncating task from 200,000 to 100,000 characters
```

### Request Size Rejection

**Request**:
```
POST /api/v1/sessions/run
Content-Length: 25000000

{ large payload }
```

**Response**: HTTP 413
```json
{
  "detail": "Request size (23.8MB) exceeds maximum allowed size (20MB)"
}
```

## Configuration

Constants in `src/api/waf_filter.py`:

```python
# Maximum text content length (100,000 characters)
MAX_TEXT_CONTENT_LENGTH: int = 100_000

# Maximum file upload size (10MB in bytes)
MAX_FILE_UPLOAD_SIZE: int = 10 * 1024 * 1024

# Maximum JSON request body size (20MB)
MAX_REQUEST_BODY_SIZE: int = 20 * 1024 * 1024
```

## Attack Scenarios Prevented

### 1. **Memory Exhaustion via Large Task**
**Attack**: Send 10MB task description to consume agent memory
```bash
curl -X POST /api/v1/sessions/run \
  -H "Content-Type: application/json" \
  -d '{"task": "'$(python -c 'print("A"*10000000)')'"}'
```
**Prevention**: Task truncated to 100,000 characters, rest discarded

### 2. **Storage Exhaustion via Large Uploads**
**Attack**: Upload 1GB file to fill disk
```bash
curl -X POST /api/v1/sessions/{id}/upload \
  -F "file=@huge_file.bin"
```
**Prevention**: Rejected with HTTP 413 if > 10MB

### 3. **Network Resource Exhaustion**
**Attack**: Send many 50MB requests to consume bandwidth/memory
```bash
for i in {1..100}; do
  curl -X POST /api/v1/sessions/run \
    -H "Content-Type: application/json" \
    -d @50mb_payload.json &
done
```
**Prevention**: All requests > 20MB rejected immediately by middleware

### 4. **Agent Context Overflow**
**Attack**: Send extremely long task to overflow agent context window
```bash
# Task with 1M characters to overflow Claude's context
curl -X POST /api/v1/sessions/run \
  -d '{"task": "Write a function...(1M chars)..."}'
```
**Prevention**: Truncated to 100,000 chars, preserving agent functionality

## Monitoring & Logging

**Warning Logs** (for truncation):
```
WARNING: WAF: Truncating task from 200,000 to 100,000 characters
WARNING: WAF: Truncating message from 150,000 to 100,000 characters
```

**Warning Logs** (for rejection):
```
WARNING: WAF: Rejected file upload - size 15.5MB exceeds limit 10.0MB
WARNING: WAF: Rejected request - body size 25.3MB exceeds limit 20.0MB
```

## Endpoints Protected

All API endpoints receive WAF protection:

**Authentication**:
- `POST /api/v1/auth/login` - Body size limit

**Sessions**:
- `POST /api/v1/sessions/run` - Task truncation + body size limit
- `POST /api/v1/sessions` - Task truncation + body size limit
- `POST /api/v1/sessions/{id}/task` - Task truncation + body size limit
- `GET /api/v1/sessions` - Body size limit (minimal impact)
- `GET /api/v1/sessions/{id}` - No significant impact (no body)

**Health**:
- `GET /api/v1/health` - No impact (no body)

## Trade-offs

### Why Truncate Instead of Reject?

**Truncation** (current approach):
- ✅ User-friendly - long tasks still work
- ✅ No disruption - legitimate large tasks succeed
- ✅ Graceful degradation
- ❌ User might not notice truncation

**Rejection** (alternative):
- ✅ Clear feedback - user knows request was too large
- ✅ Forces user to shorten task
- ❌ Breaks legitimate use cases
- ❌ Poor user experience

**Decision**: Truncate text, reject binary uploads/large bodies

### Size Limits Rationale

| Limit | Value | Rationale |
|-------|-------|-----------|
| Text Content | 100,000 chars | ~25-50 pages of text<br/>Sufficient for complex tasks<br/>Stays within agent context limits |
| File Upload | 10MB | Reasonable for code files, documents<br/>Prevents storage exhaustion<br/>Large files should use external storage |
| Request Body | 20MB | 2x file limit for JSON overhead<br/>Allows base64-encoded files with metadata<br/>Prevents network exhaustion |

## Testing

### Unit Tests Needed

Create `tests/backend/test_waf_filter.py`:

```python
# Test text truncation
def test_truncate_text_at_limit():
    text = "A" * 150_000
    result = truncate_text_content(text, "test")
    assert len(result) == 100_000

# Test request size validation
async def test_reject_oversized_request():
    with pytest.raises(HTTPException) as exc:
        validate_request_body_size(25_000_000)  # 25MB
    assert exc.value.status_code == 413

# Test model validators
def test_task_truncation_in_model():
    request = RunTaskRequest(task="A" * 150_000)
    assert len(request.task) == 100_000
```

### Integration Tests

```bash
# Test large task truncation
curl -X POST http://localhost:40080/api/v1/sessions/run \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"task": "'$(python -c 'print("A"*150000)')'"}'

# Test oversized request rejection
curl -X POST http://localhost:40080/api/v1/sessions/run \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d @25mb_payload.json
# Expected: HTTP 413
```

## Future Enhancements

1. **Rate Limiting**: Add per-user/IP rate limits
2. **Content Filtering**: Scan for malicious patterns
3. **File Type Validation**: Restrict uploads to specific types
4. **Compression Support**: Allow gzip to reduce transfer sizes
5. **Configurable Limits**: Move to `api.yaml` configuration
6. **Metrics**: Track truncation/rejection rates
7. **User Notifications**: Include truncation info in response headers

## Security Benefits

1. **DoS Protection**: Prevents resource exhaustion attacks
2. **Memory Safety**: Limits in-memory data sizes
3. **Storage Protection**: Prevents disk filling
4. **Network Efficiency**: Reduces bandwidth consumption
5. **Agent Protection**: Keeps tasks within context limits
6. **Cost Control**: Prevents excessive API costs from huge requests

---

**Date**: 2026-01-11  
**Status**: Implemented and integrated  
**Test Coverage**: Linters pass, integration tests pending
