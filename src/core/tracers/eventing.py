"""
Eventing Tracer for Ag3ntum.

Wraps another tracer and emits structured events to an asyncio queue
for real-time streaming (SSE/Web).
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..structured_output import normalize_error_value, parse_structured_output
from .base import TracerBase

logger = logging.getLogger(__name__)


class EventingTracer(TracerBase):
    """
    Tracer wrapper that emits structured events to an asyncio queue.

    This allows real-time streaming (SSE/Web) while preserving the original
    tracer output for CLI or backend logging.

    Event Delivery Guarantee:
    The event_sink is an async function that persists events to the database.
    Events are awaited for persistence BEFORE being published to the EventHub.
    This ensures that SSE subscribers will always find events either:
    1. In the queue (if subscribed before publish), or
    2. In the database (if subscribed after publish)
    """

    def __init__(
        self,
        tracer: TracerBase,
        event_queue: Optional[asyncio.Queue] = None,
        event_sink: Optional[Any] = None,  # Async callable: (event: dict) -> None
        session_id: Optional[str] = None,
        initial_sequence: int = 0,
    ) -> None:
        self._tracer = tracer
        self._event_queue = event_queue
        self._event_sink = event_sink
        self._session_id = session_id
        self._sequence = initial_sequence
        self._stream_header_buffer = ""
        self._stream_header_expected: Optional[bool] = None
        self._stream_header_wrapped = False
        self._stream_structured_fields: Optional[dict[str, str]] = None
        self._stream_full_text = ""
        self._stream_active = False
        self._last_stream_full_text = ""
        self._suppress_next_message = False
        # Track working directory for post-completion security scanning
        self._working_dir: Optional[str] = None
        # Track tool outcomes per message for computed message_status
        self._message_tool_outcomes: list[dict[str, Any]] = []
        # Track background tasks to await during shutdown
        self._pending_tasks: set[asyncio.Task] = set()

    def _create_tracked_task(self, coro) -> None:
        """Create an asyncio task and track it for cleanup during drain."""
        task = asyncio.get_running_loop().create_task(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def drain(self, timeout: float = 5.0) -> None:
        """
        Await all pending background tasks.

        Call this during shutdown to ensure events are fully persisted
        before the process exits.

        Args:
            timeout: Maximum seconds to wait for pending tasks.
        """
        if not self._pending_tasks:
            return
        pending = list(self._pending_tasks)
        logger.debug(f"Draining {len(pending)} pending EventingTracer tasks")
        done, not_done = await asyncio.wait(pending, timeout=timeout)
        if not_done:
            logger.warning(
                f"{len(not_done)} EventingTracer tasks did not complete within {timeout}s"
            )
            for task in not_done:
                task.cancel()

    def emit_event(
        self,
        event_type: str,
        data: dict[str, Any],
        persist_event: bool = True
    ) -> None:
        """Emit a structured event to the queue."""
        if self._event_queue is None:
            return

        self._sequence += 1
        event = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sequence": self._sequence,
            "session_id": self._session_id or data.get("session_id"),
        }

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def publish_and_persist():
            """
            Publish to Redis Stream first, then persist to SQLite.

            With Redis Streams (not Pub/Sub), events are durably stored in the
            stream. This eliminates the race condition that existed with Pub/Sub:

            Redis Streams Architecture:
            1. Publish to Redis Stream (~1ms) - events persist in stream
            2. Persist to SQLite (~5-50ms) - backup for long-term storage/analytics

            Why this order works:
            - Redis Streams persist events (unlike Pub/Sub which is fire-and-forget)
            - SSE consumers read from the stream starting at position "0" (beginning)
            - Even if consumer connects after publish, events are still in stream
            - No race conditions - consumers always get all events from stream

            SQLite persistence is now optional backup for:
            - Long-term analytics and reporting
            - Disaster recovery if Redis data is lost
            - Historical queries that span beyond Redis TTL
            """
            # Publish to Redis Stream FIRST for low latency real-time delivery
            # Events persist in stream - no race conditions with late subscribers
            try:
                await self._event_queue.put(event)
            except Exception as e:
                logger.error(f"Failed to publish event to Redis Stream: {e}")
                # Continue to SQLite persistence as fallback

            # Then persist to SQLite as backup/long-term storage
            if self._event_sink is not None and persist_event:
                try:
                    await self._event_sink(event)
                except Exception as e:
                    logger.warning(f"SQLite persistence failed (event is in Redis Stream): {e}")

        if loop and loop.is_running():
            self._create_tracked_task(publish_and_persist())
        else:
            # No event loop available - this shouldn't happen in normal operation
            # but can occur during shutdown or in edge cases
            logger.error(
                f"emit_event called outside async context for {event_type}. "
                "Event will be published to Redis Stream but NOT persisted to SQLite."
            )
            # Best effort: publish to Redis Stream (SQLite persistence requires async context)
            try:
                self._event_queue.put_nowait(event)
            except Exception as e:
                logger.error(f"Failed to publish event {event_type}: {e}")

    def on_agent_start(
        self,
        session_id: str,
        model: str,
        tools: list[str],
        working_dir: str,
        skills: Optional[list[str]] = None,
        task: Optional[str] = None
    ) -> None:
        # Store working_dir for post-completion security scanning
        self._working_dir = working_dir

        # Save claude_session_id to database immediately when we receive it
        # The session_id parameter here is the Claude SDK session ID
        if session_id and session_id != "unknown" and self._session_id:
            self._save_claude_session_id(session_id)

        self._tracer.on_agent_start(
            session_id=session_id,
            model=model,
            tools=tools,
            working_dir=working_dir,
            skills=skills,
            task=task,
        )
        self.emit_event(
            "agent_start",
            {
                "session_id": session_id,
                "model": model,
                "tools": tools,
                "skills": skills,
                "task": task,
            },
        )

    def _save_claude_session_id(self, claude_session_id: str) -> None:
        """
        Persist claude_session_id to database immediately (fire-and-forget).

        This captures the Claude SDK's session ID as soon as we receive the init event,
        enabling auto-resume even if execution is interrupted before completion.
        """
        async def _save():
            try:
                from ..db.database import AsyncSessionLocal
                from ..db.models import Session
                from sqlalchemy import select

                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(Session).where(Session.id == self._session_id)
                    )
                    session = result.scalar_one_or_none()
                    if session:
                        session.claude_session_id = claude_session_id
                        await db.commit()
                        logger.debug(
                            f"Saved claude_session_id for {self._session_id}: {claude_session_id}"
                        )
            except Exception as e:
                logger.warning(f"Failed to save claude_session_id: {e}")

        # Schedule in event loop (non-blocking)
        try:
            asyncio.get_running_loop()
            self._create_tracked_task(_save())
        except RuntimeError:
            # No running loop - skip (will be saved on completion anyway)
            pass

    def on_tool_start(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_id: str
    ) -> None:
        self._tracer.on_tool_start(tool_name, tool_input, tool_id)
        self.emit_event(
            "tool_start",
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_id": tool_id,
            },
        )

    def on_tool_input_ready(
        self,
        tool_name: str,
        tool_id: str,
        tool_input: dict[str, Any],
    ) -> None:
        """Emit event when complete tool input is available (after streaming completes)."""
        self.emit_event(
            "tool_input_ready",
            {
                "tool_name": tool_name,
                "tool_id": tool_id,
                "tool_input": tool_input,
            },
        )

    def on_tool_complete(
        self,
        tool_name: str,
        tool_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        # Track tool outcome for message_status computation
        error_msg = None
        if is_error:
            error_msg = str(result)[:200] if result else "Tool failed"
        self._message_tool_outcomes.append({
            "tool_name": tool_name,
            "tool_id": tool_id,
            "is_error": is_error,
            "error_message": error_msg,
        })

        self._tracer.on_tool_complete(
            tool_name=tool_name,
            tool_id=tool_id,
            result=result,
            duration_ms=duration_ms,
            is_error=is_error,
        )
        self.emit_event(
            "tool_complete",
            {
                "tool_name": tool_name,
                "tool_id": tool_id,
                "result": result,
                "duration_ms": duration_ms,
                "is_error": is_error,
            },
        )

    def _compute_message_status(self) -> tuple[str, Optional[str]]:
        """
        Compute message_status and message_error based on tool outcomes in this message.

        Returns:
            Tuple of (message_status, message_error_message)
        """
        if not self._message_tool_outcomes:
            return ("COMPLETE", None)

        errors = [t for t in self._message_tool_outcomes if t["is_error"]]
        if not errors:
            return ("COMPLETE", None)

        first_error = errors[0]["error_message"]
        if len(errors) == len(self._message_tool_outcomes):
            return ("FAILED", first_error)
        return ("PARTIAL", first_error)

    def _reset_message_tool_tracking(self) -> None:
        """Reset tool tracking for the next message."""
        self._message_tool_outcomes = []

    def on_thinking(self, thinking_text: str, is_partial: bool = False) -> None:
        self._tracer.on_thinking(thinking_text, is_partial=is_partial)
        self.emit_event(
            "thinking",
            {"text": thinking_text, "is_partial": is_partial},
            persist_event=not is_partial,  # Only persist complete thinking blocks
        )

    def on_message(self, text: str, is_partial: bool = False) -> None:
        if is_partial:
            self._stream_active = True
            body_text = self._consume_stream_text(text)
            if not body_text:
                return
            self._stream_full_text += body_text
            self._tracer.on_message(body_text, is_partial=True)
            self.emit_event(
                "message",
                {
                    "text": body_text,
                    "is_partial": True,
                    # Partial messages don't have status yet
                    "message_status": None,
                    "message_error_message": None,
                    "request_status": None,
                    "request_error_message": None,
                },
                persist_event=False,
            )
            return

        if self._stream_active:
            body_text = ""
            if text and not self._stream_full_text:
                body_text = self._consume_stream_text(text)
            if not body_text and self._stream_header_expected is None and self._stream_header_buffer:
                body_text = self._stream_header_buffer
                self._stream_header_buffer = ""
                self._stream_header_expected = False
            if body_text:
                self._stream_full_text += body_text

            structured_fields = self._stream_structured_fields
            full_text = self._stream_full_text
            if not body_text and not full_text:
                self._reset_stream_state()
                self._reset_message_tool_tracking()
                return

            # Compute message status from tool outcomes
            message_status, message_error = self._compute_message_status()

            # Get request status from agent's frontmatter
            request_status = None
            request_error = None
            if structured_fields:
                request_status = structured_fields.get("request_status")
                request_error = structured_fields.get("request_error_message")

            self._tracer.on_message(full_text, is_partial=False)
            self.emit_event(
                "message",
                {
                    "text": body_text,
                    "full_text": full_text,
                    "is_partial": False,
                    "message_status": message_status,
                    "message_error_message": message_error,
                    "request_status": request_status,
                    "request_error_message": request_error,
                },
            )
            self._last_stream_full_text = full_text
            self._suppress_next_message = bool(full_text.strip())
            self._reset_stream_state()
            self._reset_message_tool_tracking()
            return

        if not text.strip():
            return

        if self._suppress_next_message:
            if text.strip() == self._last_stream_full_text.strip():
                self._suppress_next_message = False
                self._last_stream_full_text = ""
                return
            self._suppress_next_message = False
            self._last_stream_full_text = ""

        self._tracer.on_message(text, is_partial=False)
        structured_fields = None

        fields, body = parse_structured_output(text)
        if fields:
            structured_fields = fields
            text = body

        # Compute message status from tool outcomes
        message_status, message_error = self._compute_message_status()

        # Get request status from agent's frontmatter
        request_status = None
        request_error = None
        if structured_fields:
            request_status = structured_fields.get("request_status")
            request_error = structured_fields.get("request_error_message")

        self.emit_event(
            "message",
            {
                "text": text,
                "is_partial": False,
                "message_status": message_status,
                "message_error_message": message_error,
                "request_status": request_status,
                "request_error_message": request_error,
            },
        )
        # Reset tool tracking after message
        self._reset_message_tool_tracking()

    def _reset_stream_state(self) -> None:
        self._stream_header_buffer = ""
        self._stream_header_expected = None
        self._stream_header_wrapped = False
        self._stream_structured_fields = None
        self._stream_full_text = ""
        self._stream_active = False

    def _consume_stream_text(self, text: str) -> str:
        if self._stream_header_expected is None:
            self._stream_header_buffer += text
            if len(self._stream_header_buffer) < 3:
                return ""
            if self._stream_header_buffer.startswith("```"):
                fence_end = self._stream_header_buffer.find("\n")
                if fence_end == -1:
                    return ""
                self._stream_header_wrapped = True
                self._stream_header_buffer = self._stream_header_buffer[fence_end + 1 :]
            trimmed = self._stream_header_buffer.lstrip()
            if not trimmed.startswith("---"):
                output = self._stream_header_buffer
                self._stream_header_buffer = ""
                self._stream_header_expected = False
                return output
            self._stream_header_buffer = trimmed
            self._stream_header_expected = True
            return self._extract_header_body()

        if self._stream_header_expected:
            self._stream_header_buffer += text
            return self._extract_header_body()

        return text

    def _extract_header_body(self) -> str:
        lines = self._stream_header_buffer.splitlines(keepends=True)
        if not lines:
            return ""

        header_end_index = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                header_end_index = index
                break

        if header_end_index is None:
            return ""

        header_lines = lines[1:header_end_index]
        fields: dict[str, str] = {}
        for line in header_lines:
            line_value = line.strip()
            if not line_value or ":" not in line_value:
                continue
            key, value = line_value.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if key:
                # Normalize error field to filter out placeholder values
                if key == "error":
                    value = normalize_error_value(value)
                fields[key] = value

        self._stream_structured_fields = fields or None
        self._stream_header_expected = False

        body_lines = lines[header_end_index + 1 :]
        if self._stream_header_wrapped and body_lines:
            if body_lines[0].strip().startswith("```"):
                body_lines = body_lines[1:]
        body = "".join(body_lines)
        self._stream_header_buffer = ""
        return body

    def on_metrics_update(self, metrics: dict[str, Any]) -> None:
        if hasattr(self._tracer, "on_metrics_update"):
            self._tracer.on_metrics_update(metrics)
        self.emit_event("metrics_update", metrics)

    def on_error(self, error_message: str, error_type: str = "error") -> None:
        self._tracer.on_error(error_message, error_type=error_type)
        self.emit_event(
            "error",
            {
                "message": error_message,
                "error_type": error_type,
            },
        )

    def on_agent_complete(
        self,
        status: str,
        num_turns: int,
        duration_ms: int,
        total_cost_usd: Optional[float],
        result: Optional[str],
        session_id: Optional[str] = None,
        usage: Optional[dict[str, Any]] = None,
        model: Optional[str] = None,
        cumulative_cost_usd: Optional[float] = None,
        cumulative_turns: Optional[int] = None,
        cumulative_tokens: Optional[int] = None
    ) -> None:
        self._tracer.on_agent_complete(
            status=status,
            num_turns=num_turns,
            duration_ms=duration_ms,
            total_cost_usd=total_cost_usd,
            result=result,
            session_id=session_id,
            usage=usage,
            model=model,
            cumulative_cost_usd=cumulative_cost_usd,
            cumulative_turns=cumulative_turns,
            cumulative_tokens=cumulative_tokens,
        )
        self.emit_event(
            "agent_complete",
            {
                "status": status,
                "num_turns": num_turns,
                "duration_ms": duration_ms,
                "total_cost_usd": total_cost_usd,
                "result": result,
                "session_id": session_id,
                "usage": usage,
                "model": model,
                "cumulative_cost_usd": cumulative_cost_usd,
                "cumulative_turns": cumulative_turns,
                "cumulative_tokens": cumulative_tokens,
            },
        )

        # Trigger post-completion security scan of session files
        self._trigger_security_scan(session_id or self._session_id)

    def _trigger_security_scan(self, session_id: Optional[str]) -> None:
        """
        Trigger async security scan of session workspace files.

        Scans recently modified files for sensitive data and emits
        security_alert event if any secrets are found.
        """
        if not session_id or not self._working_dir:
            return

        from pathlib import Path

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No event loop available

        async def run_scan():
            try:
                # Import here to avoid circular dependencies
                from ..security import (
                    scan_session_files,
                    is_scanner_enabled,
                )

                if not is_scanner_enabled():
                    return

                workspace_path = Path(self._working_dir)
                if not workspace_path.name == "workspace":
                    possible_workspace = workspace_path / "workspace"
                    if possible_workspace.exists():
                        workspace_path = possible_workspace

                if not workspace_path.exists():
                    logger.debug(f"Workspace not found for security scan: {workspace_path}")
                    return

                scan_result = await scan_session_files(
                    session_id=session_id,
                    workspace_path=workspace_path,
                    redact_files=True,
                )

                if scan_result.has_secrets:
                    alert_data = scan_result.to_alert_data()
                    self.emit_event(
                        "security_alert",
                        alert_data,
                        persist_event=True,  # Persist to DB for audit trail
                    )
                    logger.warning(
                        f"Security scan completed for session {session_id}: "
                        f"Found {scan_result.total_secrets} secrets in "
                        f"{scan_result.files_with_secrets} files"
                    )
                else:
                    logger.debug(
                        f"Security scan completed for session {session_id}: "
                        f"No secrets found in {scan_result.files_scanned} files"
                    )

            except Exception as e:
                logger.error(f"Security scan failed for session {session_id}: {e}")

        # Schedule the scan to run in the event loop
        self._create_tracked_task(run_scan())

    def on_output_display(
        self,
        output: Optional[str] = None,
        error: Optional[str] = None,
        comments: Optional[str] = None,
        result_files: Optional[list[str]] = None,
        status: Optional[str] = None
    ) -> None:
        self._tracer.on_output_display(
            output=output,
            error=error,
            comments=comments,
            result_files=result_files,
            status=status,
        )

    def on_profile_switch(
        self,
        profile_type: str,
        profile_name: str,
        tools: list[str],
        allow_rules_count: int = 0,
        deny_rules_count: int = 0,
        profile_path: Optional[str] = None
    ) -> None:
        self._tracer.on_profile_switch(
            profile_type=profile_type,
            profile_name=profile_name,
            tools=tools,
            allow_rules_count=allow_rules_count,
            deny_rules_count=deny_rules_count,
            profile_path=profile_path,
        )
        self.emit_event(
            "profile_switch",
            {
                "profile_type": profile_type,
                "profile_name": profile_name,
                "tools": tools,
                "allow_rules_count": allow_rules_count,
                "deny_rules_count": deny_rules_count,
            },
        )

    def on_hook_triggered(
        self,
        hook_event: str,
        tool_name: Optional[str] = None,
        decision: Optional[str] = None,
        message: Optional[str] = None
    ) -> None:
        self._tracer.on_hook_triggered(
            hook_event=hook_event,
            tool_name=tool_name,
            decision=decision,
            message=message,
        )
        self.emit_event(
            "hook_triggered",
            {
                "hook_event": hook_event,
                "tool_name": tool_name,
                "decision": decision,
                "message": message,
            },
        )

    def on_conversation_turn(
        self,
        turn_number: int,
        prompt_preview: str,
        response_preview: str,
        duration_ms: int,
        tools_used: list[str]
    ) -> None:
        self._tracer.on_conversation_turn(
            turn_number=turn_number,
            prompt_preview=prompt_preview,
            response_preview=response_preview,
            duration_ms=duration_ms,
            tools_used=tools_used,
        )
        self.emit_event(
            "conversation_turn",
            {
                "turn_number": turn_number,
                "prompt_preview": prompt_preview,
                "response_preview": response_preview,
                "duration_ms": duration_ms,
                "tools_used": tools_used,
            },
        )

    def on_session_connect(self, session_id: Optional[str] = None) -> None:
        self._tracer.on_session_connect(session_id=session_id)
        self.emit_event("session_connect", {"session_id": session_id})

    def on_session_disconnect(
        self,
        session_id: Optional[str] = None,
        total_turns: int = 0,
        total_duration_ms: int = 0
    ) -> None:
        self._tracer.on_session_disconnect(
            session_id=session_id,
            total_turns=total_turns,
            total_duration_ms=total_duration_ms,
        )
        self.emit_event(
            "session_disconnect",
            {
                "session_id": session_id,
                "total_turns": total_turns,
                "total_duration_ms": total_duration_ms,
            },
        )

    def on_subagent_start(
        self,
        task_id: str,
        subagent_name: str,
        prompt: str
    ) -> None:
        self._tracer.on_subagent_start(task_id, subagent_name, prompt)
        self.emit_event(
            "subagent_start",
            {
                "task_id": task_id,
                "subagent_name": subagent_name,
                "prompt_preview": prompt[:200] if prompt else "",
            },
        )

    def on_subagent_message(
        self,
        task_id: str,
        text: str,
        is_partial: bool = False
    ) -> None:
        self._tracer.on_subagent_message(task_id, text, is_partial)
        self.emit_event(
            "subagent_message",
            {
                "task_id": task_id,
                "text": text,
                "is_partial": is_partial,
            },
            persist_event=not is_partial,
        )

    def on_subagent_stop(
        self,
        task_id: str,
        result: Any,
        duration_ms: int,
        is_error: bool
    ) -> None:
        self._tracer.on_subagent_stop(task_id, result, duration_ms, is_error)
        self.emit_event(
            "subagent_stop",
            {
                "task_id": task_id,
                "result_preview": str(result)[:500] if result else "",
                "duration_ms": duration_ms,
                "is_error": is_error,
            },
        )
