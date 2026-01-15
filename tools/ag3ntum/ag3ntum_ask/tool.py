"""
Ag3ntumAskUserQuestion - Interactive user question tool (Human-in-the-Loop).

This MCP tool allows the agent to ask interactive questions to users via the web UI.
The tool STOPS execution and the session enters "waiting_for_input" status.
The user can answer hours or days later, and the session can be resumed.

Architecture (event-based, session stop/resume):
1. Tool is called with question data (questions, options, etc.)
2. Tool emits a "question_pending" event to the session event stream
3. Tool returns a STOP signal - agent execution ends gracefully
4. Session status changes to "waiting_for_input"
5. Frontend sees the question_pending event and displays interactive UI
6. User selects options and clicks submit (can take hours/days)
7. Frontend POSTs answer to /api/v1/sessions/{session_id}/answer
8. API emits a "question_answered" event
9. User resumes session - agent continues with answer in context

Events are stored in the same event table as all other session events,
providing a unified history and natural fit with Claude Code's resume capability.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from claude_agent_sdk import create_sdk_mcp_server, tool

logger = logging.getLogger(__name__)

# Tool name constant
AG3NTUM_ASK_TOOL: str = "mcp__ag3ntum__AskUserQuestion"

# Event types for human-in-the-loop
EVENT_TYPE_QUESTION_PENDING = "question_pending"
EVENT_TYPE_QUESTION_ANSWERED = "question_answered"

# Input validation limits
MAX_QUESTION_TEXT_LENGTH = 2000  # Max length for question text
MAX_HEADER_LENGTH = 50  # Max length for question header
MAX_OPTION_LABEL_LENGTH = 200  # Max length for option labels
MAX_OPTION_DESCRIPTION_LENGTH = 500  # Max length for option descriptions
MAX_OPTIONS_PER_QUESTION = 10  # Max number of options per question
MAX_QUESTIONS = 10  # Max number of questions per tool call


def create_ask_user_question_tool(session_id: str):
    """
    Create AskUserQuestion tool bound to a specific session.

    Args:
        session_id: The session ID (used to correlate questions/answers).

    Returns:
        Tool function decorated with @tool.
    """
    bound_session_id = session_id

    @tool(
        "AskUserQuestion",
        """Ask the user interactive questions with multiple choice options and additional custom text.

This tool displays questions to the user in the UI.
IMPORTANT: After calling this tool, the agent execution will STOP.
The user can take as long as needed to answer (hours or days).
When the user answers, the session will be resumed with their response.

Args:
    questions: Array of question objects, each containing:
        - question: The question text to display
        - header: Short label/category for the question (max 12 chars)
        - options: Array of option objects with:
            - label: The option text (1-5 words)
            - description: Optional explanation of this option
        - multiSelect: Boolean, if true allows multiple selections

Returns:
    A message indicating the session will pause for user input.
    When resumed, you will receive the user's answers in the context with selected options and additional comments or text.

Example:
    AskUserQuestion(questions=[
        {
            "question": "What programming language would you like to use?",
            "header": "Language",
            "options": [
                {"label": "Python", "description": "Great for beginners"},
                {"label": "JavaScript", "description": "Web development"}
            ],
            "multiSelect": false
        }
    ])
""",
        {
            "questions": list,
        },
    )
    async def ask_user_question(args: dict[str, Any]) -> dict[str, Any]:
        """Ask user questions - STOPS execution until answered and resumed."""
        questions = args.get("questions", [])

        if not questions:
            return _error("questions array is required")

        # Handle JSON string input (MCP tools may receive serialized JSON)
        if isinstance(questions, str):
            try:
                questions = json.loads(questions)
            except json.JSONDecodeError as e:
                return _error(f"Failed to parse questions JSON: {e}")

        if not isinstance(questions, list):
            return _error("questions must be an array")

        # Validate question count
        if len(questions) > MAX_QUESTIONS:
            return _error(f"Too many questions ({len(questions)}). Maximum is {MAX_QUESTIONS}.")

        # Validate question format and sanitize input lengths
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                return _error(f"Question {i} must be an object")
            if "question" not in q:
                return _error(f"Question {i} missing 'question' field")
            if "options" not in q or not isinstance(q.get("options"), list):
                return _error(f"Question {i} missing or invalid 'options' array")
            if len(q["options"]) < 2:
                return _error(f"Question {i} must have at least 2 options")
            if len(q["options"]) > MAX_OPTIONS_PER_QUESTION:
                return _error(f"Question {i} has too many options ({len(q['options'])}). Maximum is {MAX_OPTIONS_PER_QUESTION}.")

            # Validate and truncate question text length
            question_text = q.get("question", "")
            if len(question_text) > MAX_QUESTION_TEXT_LENGTH:
                q["question"] = question_text[:MAX_QUESTION_TEXT_LENGTH]
                logger.warning(f"Truncated question {i} text from {len(question_text)} to {MAX_QUESTION_TEXT_LENGTH} chars")

            # Validate and truncate header length
            if "header" in q and q["header"]:
                header = str(q["header"])
                if len(header) > MAX_HEADER_LENGTH:
                    q["header"] = header[:MAX_HEADER_LENGTH]

            # Validate each option
            for j, opt in enumerate(q["options"]):
                if not isinstance(opt, dict):
                    return _error(f"Question {i}, option {j} must be an object")
                if "label" not in opt:
                    return _error(f"Question {i}, option {j} missing 'label' field")

                # Truncate option label if too long
                label = str(opt.get("label", ""))
                if len(label) > MAX_OPTION_LABEL_LENGTH:
                    opt["label"] = label[:MAX_OPTION_LABEL_LENGTH]

                # Truncate description if too long
                if "description" in opt and opt["description"]:
                    desc = str(opt["description"])
                    if len(desc) > MAX_OPTION_DESCRIPTION_LENGTH:
                        opt["description"] = desc[:MAX_OPTION_DESCRIPTION_LENGTH]

        # Generate unique question ID
        question_id = str(uuid.uuid4())

        # Emit question_pending event
        # This will be recorded in the session's event stream
        try:
            from src.services import event_service
            from src.services.agent_runner import agent_runner

            event = {
                "type": EVENT_TYPE_QUESTION_PENDING,
                "data": {
                    "question_id": question_id,
                    "questions": questions,
                    "session_id": bound_session_id,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sequence": await event_service.get_last_sequence(bound_session_id) + 1,
                "session_id": bound_session_id,
            }

            # Record event to database
            await event_service.record_event(event)

            # Publish to SSE subscribers
            await agent_runner.publish_event(bound_session_id, event)

            logger.info(
                f"AskUserQuestion: Emitted question_pending event {question_id} "
                f"for session {bound_session_id}"
            )
        except Exception as e:
            logger.error(f"Failed to emit question_pending event: {e}")
            return _error(f"Failed to store question: {e}")

        # Format question summary for the result
        question_summary = []
        for i, q in enumerate(questions):
            opts = ", ".join(opt.get("label", "?") for opt in q.get("options", []))
            question_summary.append(f"Q{i+1}: {q.get('question', '?')} (Options: {opts})")

        # Return result that signals session should stop
        # The agent will see this message and execution will end
        # The frontend will detect the pending question and show UI
        return {
            "content": [{
                "type": "text",
                "text": (
                    f"I have asked the user the following question(s):\n\n"
                    f"{chr(10).join(question_summary)}\n\n"
                    f"The session will now pause and wait for the user's response. "
                    f"This is a human-in-the-loop interaction - the user can take as long as they need. "
                    f"When they answer, the session will be resumed and I will continue with their response.\n\n"
                    f"[Question ID: {question_id}]"
                )
            }],
            # These fields signal that the session should stop
            "_stop_session": True,
            "_stop_reason": "waiting_for_user_input",
            "_question_id": question_id,
        }

    return ask_user_question


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {"content": [{"type": "text", "text": f"**Error:** {message}"}], "isError": True}


# API functions for use by the sessions endpoint
# These query the event stream for question/answer state

async def get_pending_question_from_events(session_id: str) -> Optional[dict[str, Any]]:
    """
    Get pending question for a session by reading from events.

    Looks for the latest question_pending event that doesn't have
    a corresponding question_answered event.

    Args:
        session_id: The session ID.

    Returns:
        Question data dict or None if no pending question.
    """
    from src.services import event_service

    events = await event_service.list_events(session_id)

    # Find all question_pending and question_answered events
    pending_questions: dict[str, dict] = {}
    answered_questions: set[str] = set()

    for event in events:
        event_type = event.get("type")
        data = event.get("data", {})

        if event_type == EVENT_TYPE_QUESTION_PENDING:
            question_id = data.get("question_id")
            if question_id:
                pending_questions[question_id] = {
                    "question_id": question_id,
                    "questions": data.get("questions", []),
                    "timestamp": event.get("timestamp"),
                }

        elif event_type == EVENT_TYPE_QUESTION_ANSWERED:
            question_id = data.get("question_id")
            if question_id:
                answered_questions.add(question_id)

    # Find unanswered questions
    for question_id, question_data in pending_questions.items():
        if question_id not in answered_questions:
            return question_data

    return None


async def submit_answer_as_event(
    session_id: str,
    question_id: str,
    answer: str,
) -> bool:
    """
    Submit an answer by emitting a question_answered event.

    Args:
        session_id: The session ID.
        question_id: The question ID (or "latest").
        answer: The user's answer.

    Returns:
        True if answer was submitted successfully, False if question not found.
    """
    from src.services import event_service
    from src.services.agent_runner import agent_runner

    # Handle "latest" - find the most recent pending question
    if question_id == "latest":
        pending = await get_pending_question_from_events(session_id)
        if not pending:
            logger.warning(f"No pending question found for session {session_id}")
            return False
        question_id = pending["question_id"]

    # Verify the question exists and is pending
    pending = await get_pending_question_from_events(session_id)
    if not pending or pending.get("question_id") != question_id:
        # Check if it was a different pending question
        if not pending:
            logger.warning(f"No pending question for session {session_id}")
            return False

    try:
        event = {
            "type": EVENT_TYPE_QUESTION_ANSWERED,
            "data": {
                "question_id": question_id,
                "answer": answer,
                "session_id": session_id,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sequence": await event_service.get_last_sequence(session_id) + 1,
            "session_id": session_id,
        }

        # Record event to database
        await event_service.record_event(event)

        # Publish to SSE subscribers
        await agent_runner.publish_event(session_id, event)

        logger.info(f"Answer submitted for question {question_id}: {answer[:100]}...")
        return True

    except Exception as e:
        logger.error(f"Failed to submit answer as event: {e}")
        return False


async def get_answered_questions_from_events(session_id: str) -> list[dict[str, Any]]:
    """
    Get all answered questions for building resume context.

    Returns list of dicts with question text and answer.
    """
    from src.services import event_service

    events = await event_service.list_events(session_id)

    # Build mapping of questions and answers
    questions: dict[str, dict] = {}
    answers: dict[str, str] = {}

    for event in events:
        event_type = event.get("type")
        data = event.get("data", {})

        if event_type == EVENT_TYPE_QUESTION_PENDING:
            question_id = data.get("question_id")
            if question_id:
                questions[question_id] = {
                    "question_id": question_id,
                    "questions": data.get("questions", []),
                }

        elif event_type == EVENT_TYPE_QUESTION_ANSWERED:
            question_id = data.get("question_id")
            if question_id:
                answers[question_id] = data.get("answer", "")

    # Return answered questions with their answers
    result = []
    for question_id, question_data in questions.items():
        if question_id in answers:
            result.append({
                "question_id": question_id,
                "questions": question_data["questions"],
                "answer": answers[question_id],
            })

    return result


# Sync wrappers for backward compatibility (deprecated)
def get_pending_question(session_id: str, question_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Get pending question (sync wrapper - deprecated, use async version)."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run,
                get_pending_question_from_events(session_id)
            )
            return future.result(timeout=10)
    except RuntimeError:
        return asyncio.run(get_pending_question_from_events(session_id))


def submit_answer(session_id: str, question_id: str, answer: str) -> bool:
    """Submit answer (sync wrapper - deprecated, use async version)."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run,
                submit_answer_as_event(session_id, question_id, answer)
            )
            return future.result(timeout=10)
    except RuntimeError:
        return asyncio.run(submit_answer_as_event(session_id, question_id, answer))


def create_ag3ntum_ask_mcp_server(
    session_id: str,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
):
    """
    Create an in-process MCP server for the AskUserQuestion tool.

    Args:
        session_id: The session ID for correlating questions/answers.
        server_name: MCP server name.
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    ask_tool = create_ask_user_question_tool(session_id=session_id)

    logger.info(f"Created AskUserQuestion MCP server for session {session_id}")

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[ask_tool],
    )
