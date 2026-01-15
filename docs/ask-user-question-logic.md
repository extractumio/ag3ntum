# AskUserQuestion Control Flow (Human-in-the-Loop)

This document describes the complete human-in-the-loop interaction flow for the AskUserQuestion tool.

## Overview

The AskUserQuestion tool enables human-in-the-loop interactions where the agent can ask the user questions and wait for their response before continuing. The user can take as long as needed to respond (minutes, hours, or days), and the session will resume with their answer.

## Architecture

### Event-Based Design

The system uses events stored in the database to track question/answer state:

| Event Type | Description |
|------------|-------------|
| `question_pending` | Agent has asked a question, waiting for user response |
| `question_answered` | User has submitted their answer |

### Key Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Backend                                     │
├─────────────────────────────────────────────────────────────────────────┤
│  tools/ag3ntum/ag3ntum_ask/tool.py                                      │
│    - MCP tool implementation                                            │
│    - Emits question_pending event                                       │
│    - Returns _stop_session signal                                       │
│                                                                         │
│  src/api/routes/sessions.py                                             │
│    - POST /answer endpoint                                              │
│    - Resume context builder (with answered questions)                   │
│    - Wraps context in <resume-context> tags                            │
│                                                                         │
│  src/services/agent_runner.py                                           │
│    - Handles _stop_session signal                                       │
│    - Sets session status to waiting_for_input                           │
├─────────────────────────────────────────────────────────────────────────┤
│                              Frontend                                    │
├─────────────────────────────────────────────────────────────────────────┤
│  src/web_terminal_client/src/App.tsx                                    │
│    - AskUserQuestionBlock component (form UI)                           │
│    - Buffering logic (prevents flickering)                              │
│    - stripResumeContext() for hiding LLM-only content                   │
│    - handleSubmitAnswer() - POSTs to /answer endpoint                   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Complete Event Flow

### Phase 1: Agent Asks Question

```
1. Agent calls AskUserQuestion tool with question data
2. Tool validates input and generates question_id (UUID)
3. Tool emits "question_pending" event to database + SSE
4. Tool returns result with _stop_session: true
5. Agent runner detects stop signal
6. Session status changes to "waiting_for_input"
7. Agent turn ends gracefully
```

### Phase 2: Frontend Displays Form

```
1. SSE stream delivers events to frontend
2. tool_start event → buffered (not attached to message yet)
3. tool_input_ready event → updates buffered tool input
4. tool_complete event → updates buffered tool status
5. agent_complete event → FLUSH: attach buffered tools to lastAgentMessage
6. AskUserQuestionBlock renders inline in the agent message
7. Form is interactive (sessionStatus === 'waiting_for_input')
```

### Phase 3: User Answers

```
1. User selects options in the form
2. User optionally adds comments in textarea
3. User clicks "[ Submit Answer ]"
4. Frontend POSTs to /api/v1/sessions/{session_id}/answer
5. Backend emits "question_answered" event
6. Form becomes read-only (hasAnswered state)
```

### Phase 4: Session Resume

```
1. User clicks "Resume" button
2. Backend builds resume context with:
   - Previous execution state
   - Answered questions (Q&A pairs)
   - Todo state at pause
3. Context wrapped in <resume-context>...</resume-context> tags
4. Agent continues with answer in context
5. Frontend strips <resume-context> tags from display
```

## Frontend Implementation (App.tsx)

### Buffering Algorithm

The key innovation is buffering AskUserQuestion tools to prevent flickering during streaming:

```typescript
conversation = useMemo(() => {
  // Buffer for AskUserQuestion tools - flushed on agent_complete
  let bufferedAskUserQuestions: ToolCallView[] = []
  let lastAgentMessage: ConversationItem | null = null

  for each event:
    case 'tool_start':
      if (isAskUserQuestion(toolName)):
        bufferedAskUserQuestions.push(newTool)  // Buffer, don't attach yet
      else:
        attach to lastAgentMessage or currentStreamMessage

    case 'tool_input_ready':
      if (isAskUserQuestion(toolName)):
        update tool.input in bufferedAskUserQuestions
      else:
        update tool in messages

    case 'tool_complete':
      if (isAskUserQuestion(toolName)):
        update tool.status in bufferedAskUserQuestions
      else:
        update tool in messages

    case 'agent_complete':
      // NOW flush buffered tools - streaming is done
      if (bufferedAskUserQuestions.length > 0):
        attach all to lastAgentMessage (or create new message)
        bufferedAskUserQuestions = []

  // NO flush at end - agent_complete handler does the flush
  return items
}, [events])
```

### Why Buffering?

**Problem:** During streaming, the `useMemo` recalculates on every new event. If we attached AskUserQuestion tools immediately, the form would "jump" between messages as new events arrived.

**Solution:**
1. Buffer AskUserQuestion tools separately during streaming
2. Only flush (attach to message) when `agent_complete` event is received
3. Form appears exactly once, at the end of streaming, in the correct position

### Hiding Resume Context

The `<resume-context>` content is for the LLM only, not for display:

```typescript
// Strip <resume-context>...</resume-context> from display
function stripResumeContext(text: string): string {
  return text.replace(/<resume-context>[\s\S]*?<\/resume-context>\s*/g, '').trim();
}

// Used in both MessageBlock (user messages) and AgentMessageBlock
const displayContent = stripResumeContext(content);
```

## Backend Implementation

### Resume Context Builder (sessions.py)

When resuming a session, the backend builds context for the LLM:

```python
# Build context wrapped in resume-context tags so it's not shown in UI
context_lines = ["<resume-context>"]

if is_waiting_for_input:
    context_lines.append("Previous execution paused waiting for user input.")

# Include answered questions
answered_questions = await get_answered_questions_from_events(session_id)
if answered_questions:
    context_lines.append("User answered the following questions:")
    for aq in answered_questions:
        for q in aq.get("questions", []):
            context_lines.append(f"  Q: {q.get('question')}")
        context_lines.append(f"  A: {aq.get('answer')}")

# Include todo state
if todos:
    context_lines.append("Todo state at pause:")
    for todo in todos:
        context_lines.append(f"  {status_icon} {content} [{status}]")

context_lines.append("</resume-context>")
```

### MCP Tool Implementation (tool.py)

The AskUserQuestion tool:

```python
@tool("AskUserQuestion", ...)
async def ask_user_question(args: dict[str, Any]) -> dict[str, Any]:
    questions = args.get("questions", [])
    question_id = str(uuid.uuid4())

    # Emit question_pending event
    event = {
        "type": "question_pending",
        "data": {
            "question_id": question_id,
            "questions": questions,
            "session_id": session_id,
        },
    }
    await event_service.record_event(event)
    await agent_runner.publish_event(session_id, event)

    # Return stop signal
    return {
        "content": [{"type": "text", "text": "..."}],
        "_stop_session": True,
        "_stop_reason": "waiting_for_user_input",
        "_question_id": question_id,
    }
```

## Session Status Values

| Status | Description |
|--------|-------------|
| `running` | Agent is actively processing |
| `waiting_for_input` | Agent paused, waiting for user to answer question |
| `complete` | Agent finished successfully |
| `failed` | Agent encountered an error |
| `cancelled` | User cancelled the session |

## Component Hierarchy

```
App
└── ConversationList
    ├── MessageBlock (user messages)
    │   └── displayContent = stripResumeContext(content)
    │
    └── AgentMessageBlock
        ├── displayContent = stripResumeContext(content)
        └── AskUserQuestionBlock (inline=true)
            ├── Question text
            ├── Option buttons (clickable when interactive)
            ├── Additional comments textarea
            └── [ Submit Answer ] button
```

## AskUserQuestionBlock Interactivity

The form is interactive when:
- `tool.status === 'running'` - tool is still executing, OR
- `sessionStatus === 'waiting_for_input'` - session is waiting for user response

The form becomes read-only when:
- User has already submitted an answer (`hasAnswered` state)
- Tool status is `complete` with answer in output
- Tool status is `failed`

## API Endpoints

### POST /api/v1/sessions/{session_id}/answer

Submit user's answer to a pending question.

**Request:**
```json
{
  "question_id": "uuid-or-latest",
  "answer": "Python\nBeginner"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Answer submitted"
}
```

### GET /api/v1/sessions/{session_id}/pending-question

Check if session has a pending question.

**Response:**
```json
{
  "has_pending_question": true,
  "question_id": "uuid",
  "questions": [...],
  "created_at": "2024-01-15T..."
}
```

## Styling

CSS classes for the AskUserQuestion form:

```css
.ask-user-question-inline { ... }
.ask-question-card { ... }
.ask-option-button { ... }
.ask-option-button.selected { ... }
.ask-submit-button { ... }
.ask-additional-comments-textarea { ... }
```

The submit button uses bracket styling to match ReactJS terminal UI:
```css
.ask-submit-button {
  /* Renders as: [ Submit Answer ] */
}
```
