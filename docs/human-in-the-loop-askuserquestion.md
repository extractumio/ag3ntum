# Human-in-the-Loop AskUserQuestion Architecture

## Overview

This document describes the architecture for implementing a proper human-in-the-loop flow for `mcp__ag3ntum__AskUserQuestion` where:

1. Agent execution **STOPS** (not pauses/polls) when AskUserQuestion is called
2. Question is stored in the **database** (not files)
3. User can answer **hours/days later** via frontend
4. Session can be **RESUMED** with the answer (using Claude Code's resume capability)

## Current Problem

The current implementation has issues:
- Uses file-based IPC (pending_question.json/answer.json) - unreliable across processes
- Uses polling with 5-minute timeout - not suitable for long waits
- MCP subprocess doesn't share memory with API server - causes "no pending question" errors

## Proposed Architecture

### Key Insight: Session STOP, not PAUSE

When AskUserQuestion is called:
1. Tool stores question in database
2. Tool returns a **special stop result** that tells the agent to STOP execution
3. Session status changes to `"waiting_for_input"`
4. User answers via frontend at any time
5. Session is **RESUMED** with answer in context

This leverages Claude Code's existing resume capability instead of fighting against it.

### Database Schema

Add new `PendingQuestion` table:

```python
class PendingQuestion(Base):
    """Pending questions waiting for user input (human-in-the-loop)."""
    __tablename__ = "pending_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    session_id: Mapped[str] = mapped_column(
        String(50), ForeignKey("sessions.id"), index=True
    )
    question_data: Mapped[str] = mapped_column(Text)  # JSON: questions array
    answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # Status: pending, answered, expired, cancelled

    created_at: Mapped[datetime] = mapped_column(DateTime)
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    session: Mapped["Session"] = relationship("Session")
```

### Session Status Addition

Add new status `"waiting_for_input"`:
- Session is not running
- Session is resumable
- There is a pending question to answer

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                     AGENT EXECUTION FLOW                            │
└─────────────────────────────────────────────────────────────────────┘

1. Agent Running
   │
   ▼
2. Agent calls mcp__ag3ntum__AskUserQuestion(questions=[...])
   │
   ▼
3. Tool Implementation:
   ├── Creates PendingQuestion record in DB (status="pending")
   ├── Emits "question_pending" event via SSE
   └── Returns special result: {"stop_session": true, "question_id": "..."}
   │
   ▼
4. Agent receives stop signal
   ├── Agent SDK interprets as task completion
   └── Session status → "waiting_for_input"
   │
   ▼
5. Frontend sees "question_pending" event
   ├── Displays question UI in message stream
   └── User can take hours/days to respond

   │ (user answers)
   ▼
6. Frontend calls POST /sessions/{id}/answer
   ├── Updates PendingQuestion.answer, status="answered"
   └── Emits "question_answered" event
   │
   ▼
7. Frontend calls POST /sessions/{id}/task (resume)
   ├── Builds resume context including the answer
   ├── Uses Claude Code resume_session_id
   └── Agent continues with answer in context
   │
   ▼
8. Session status → "running"
   Agent continues execution with user's answer
```

### API Changes

#### GET /sessions/{id}
Add field to SessionResponse:
```python
class SessionResponse(BaseModel):
    # ... existing fields ...
    pending_question: Optional[PendingQuestionResponse] = None
```

#### POST /sessions/{id}/answer (update existing)
```python
class SubmitAnswerRequest(BaseModel):
    question_id: str
    answer: str  # Selected option(s), comma-separated for multiSelect

class SubmitAnswerResponse(BaseModel):
    success: bool
    message: str
    can_resume: bool  # True if session can be resumed now
```

#### GET /sessions/{id}/pending-question (new)
Returns current pending question for a session if any.

### Tool Implementation

```python
@tool("AskUserQuestion", ...)
async def ask_user_question(args: dict[str, Any]) -> dict[str, Any]:
    """Ask user questions - STOPS execution until answered."""
    questions = args.get("questions", [])

    # Validate questions...

    # Store in database
    question_id = str(uuid.uuid4())
    async with get_db_session() as db:
        pending = PendingQuestion(
            id=question_id,
            session_id=bound_session_id,
            question_data=json.dumps(questions),
            status="pending",
            created_at=datetime.now(timezone.utc),
        )
        db.add(pending)
        await db.commit()

    # Emit event for frontend
    emit_event({
        "type": "question_pending",
        "data": {
            "question_id": question_id,
            "questions": questions,
        }
    })

    # Return stop signal - agent will stop execution
    return {
        "content": [{
            "type": "text",
            "text": (
                "I have asked the user a question and am waiting for their response. "
                "The session will now pause. When the user answers, the session will resume "
                "and I will continue with their response."
            )
        }],
        "stop_reason": "waiting_for_user_input",
        "question_id": question_id,
    }
```

### Resume Context Enhancement

Update `build_resume_context()` to include pending question answers:

```python
async def build_resume_context(session_id: str) -> tuple[str | None, bool]:
    # ... existing code ...

    # Check for answered questions
    async with get_db_session() as db:
        result = await db.execute(
            select(PendingQuestion)
            .where(PendingQuestion.session_id == session_id)
            .where(PendingQuestion.status == "answered")
            .order_by(PendingQuestion.answered_at.desc())
        )
        answered_questions = result.scalars().all()

    if answered_questions:
        context_lines.append("")
        context_lines.append("User answered the following questions:")
        for q in answered_questions:
            questions_data = json.loads(q.question_data)
            context_lines.append(f"  Q: {questions_data[0].get('question', 'Unknown')}")
            context_lines.append(f"  A: {q.answer}")
            context_lines.append("")

    return "\n".join(context_lines), True
```

### Frontend Changes

#### Message Stream
- Detect `question_pending` event
- Render inline question UI (already implemented)
- On submit: call `/answer` then `/task` to resume

#### Session List
- Show "Waiting for Input" badge for sessions with `status="waiting_for_input"`
- Allow resuming from session list after answering

### Event Types

New events:
- `question_pending` - Question was asked, waiting for answer
- `question_answered` - User provided an answer

### Edge Cases

1. **Multiple questions in one call**: Store as single PendingQuestion with array of questions
2. **Session cancelled while waiting**: Mark PendingQuestion as "cancelled"
3. **Session already has pending question**: Return error (one at a time)
4. **User refreshes page**: Question persisted in DB, UI re-renders from events
5. **Server restart**: Questions in DB survive, sessions can still be resumed

### Migration

1. Add `pending_questions` table via Alembic migration
2. Add `waiting_for_input` to valid session statuses
3. Update frontend to handle new flow
4. Deprecate file-based IPC (pending_question.json, answer.json)

### Benefits

1. **No timeouts**: User can take days to answer
2. **Database persistence**: Survives restarts, process boundaries
3. **Leverages existing resume**: Uses Claude Code's proven resume capability
4. **History preserved**: All questions and answers stored in DB
5. **Simpler architecture**: No polling loops, no Redis waits
