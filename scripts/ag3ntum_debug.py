#!/usr/bin/env python3
"""
Ag3ntum Debug CLI - Test agent requests with comprehensive tracing.

Connects to running Ag3ntum Docker container, sends requests, streams SSE events,
and provides detailed analysis of security behavior.

Usage:
    python scripts/ag3ntum_debug.py -r "Write test.txt with hello"
    python scripts/ag3ntum_debug.py -r "Read /etc/passwd" --security-only
    python scripts/ag3ntum_debug.py -r "List files" --verbose --dump-session
"""
import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax

console = Console()


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ToolCall:
    """Represents a tool call from the agent."""

    name: str
    input: dict[str, Any]
    result: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    blocked: bool = False
    block_reason: str | None = None


@dataclass
class DebugResult:
    """Complete result of a debug run."""

    session_id: str
    user: str
    request: str
    events: list[dict] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    final_status: str = "unknown"
    final_message: str = ""
    error: str | None = None
    duration_ms: int | None = None
    session_path: Path | None = None


@dataclass
class AnalysisReport:
    """Security analysis of the debug run."""

    allowed_operations: list[ToolCall] = field(default_factory=list)
    blocked_operations: list[ToolCall] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    security_events: list[dict] = field(default_factory=list)


# =============================================================================
# Debug CLI Class
# =============================================================================


class Ag3ntumDebugCLI:
    """CLI tool for debugging agent security."""

    def __init__(self, host: str = "localhost", port: int = 40080):
        self.base_url = f"http://{host}:{port}"
        self.api_url = f"{self.base_url}/api/v1"
        self.token: str | None = None
        self.username: str | None = None
        self.users_dir = Path("users")  # Relative to project root

    async def authenticate(self, email: str, password: str = "test123") -> bool:
        """Authenticate and get access token, then fetch username."""
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.api_url}/auth/login",
                    json={"email": email, "password": password},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self.token = data["access_token"]
                    
                    # Fetch actual username from /auth/me
                    me_resp = await client.get(
                        f"{self.api_url}/auth/me",
                        headers={"Authorization": f"Bearer {self.token}"},
                    )
                    if me_resp.status_code == 200:
                        user_data = me_resp.json()
                        self.username = user_data["username"]
                    else:
                        # Fallback: use email as username
                        self.username = email
                    
                    return True
                console.print(
                    f"[red]Auth failed: {resp.status_code} - {resp.text}[/red]"
                )
                return False
            except httpx.ConnectError:
                console.print(
                    f"[red]Connection failed: Could not connect to {self.base_url}[/red]"
                )
                console.print(
                    "[yellow]Make sure the Ag3ntum Docker container is running.[/yellow]"
                )
                return False
            except Exception as e:
                console.print(f"[red]Auth error: {e}[/red]")
                return False

    async def create_session(self) -> str | None:
        """Create a new session and return session ID."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.api_url}/sessions",
                headers={"Authorization": f"Bearer {self.token}"},
                json={},
            )
            if resp.status_code in (200, 201):
                return resp.json()["id"]
            return None

    async def run_request(
        self, request: str, user: str = "greg", password: str = "test123"
    ) -> DebugResult:
        """
        Execute a request and collect all events.

        Flow:
            1. Authenticate as user (email or username)
            2. Create new session via POST /sessions/run
            3. Stream SSE events from GET /sessions/{id}/events
            4. Collect tool calls and results
            5. Return comprehensive result
        """
        result = DebugResult(session_id="", user=user, request=request)

        # Step 1: Authenticate
        if not await self.authenticate(user, password):
            result.error = "Authentication failed"
            return result

        # Update result with actual username from API
        result.user = self.username
        console.print(f"[green]✓[/green] Authenticated as [bold]{self.username}[/bold]")

        # Step 2: Create session and start task
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.api_url}/sessions/run",
                headers={"Authorization": f"Bearer {self.token}"},
                json={"task": request},
            )
            if resp.status_code not in (200, 201):
                result.error = (
                    f"Failed to start task: {resp.status_code} - {resp.text}"
                )
                return result

            data = resp.json()
            result.session_id = data["session_id"]
            # Use actual username from API, not the login parameter
            result.session_path = self.users_dir / self.username / "sessions" / result.session_id

        console.print(f"[green]✓[/green] Session: [bold]{result.session_id}[/bold]")
        console.print("[dim]Streaming events...[/dim]")

        # Step 3: Stream SSE events
        start_time = datetime.now()
        current_tool: ToolCall | None = None

        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                async with client.stream(
                    "GET",
                    f"{self.api_url}/sessions/{result.session_id}/events",
                    params={"token": self.token},
                ) as response:
                    async for line in response.aiter_lines():
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data: "):
                            try:
                                event = json.loads(line[6:])
                                result.events.append(event)

                                # Process event
                                event_type = event.get("type")
                                event_data = event.get("data", {})

                                if event_type == "tool_start":
                                    current_tool = ToolCall(
                                        name=event_data.get("name", "unknown"),
                                        input=event_data.get("tool_input", {}),
                                    )

                                elif event_type == "tool_complete":
                                    if current_tool:
                                        current_tool.result = event_data.get(
                                            "result", ""
                                        )
                                        current_tool.error = event_data.get("error")
                                        current_tool.duration_ms = event_data.get(
                                            "duration_ms"
                                        )
                                        # Check if blocked
                                        result_str = str(event_data).lower()
                                        if (
                                            "blocked" in result_str
                                            or "denied" in result_str
                                            or "validation failed" in result_str
                                            or "outside workspace" in result_str
                                        ):
                                            current_tool.blocked = True
                                            current_tool.block_reason = event_data.get(
                                                "error"
                                            ) or str(event_data.get("result", ""))[:200]
                                        result.tool_calls.append(current_tool)
                                        current_tool = None

                                elif event_type == "error":
                                    result.error = event_data.get(
                                        "message", str(event_data)
                                    )

                                elif event_type == "message":
                                    text = event_data.get("text", "")
                                    if text and not event_data.get("is_partial"):
                                        result.final_message = text

                                elif event_type in ("agent_complete", "cancelled"):
                                    result.final_status = event_type
                                    break

                            except json.JSONDecodeError:
                                pass
            except httpx.ReadTimeout:
                result.error = "Timeout waiting for agent response"

        result.duration_ms = int(
            (datetime.now() - start_time).total_seconds() * 1000
        )
        return result

    def analyze_result(self, result: DebugResult) -> AnalysisReport:
        """Analyze the result for security-relevant information."""
        report = AnalysisReport()

        for tool_call in result.tool_calls:
            if tool_call.blocked:
                report.blocked_operations.append(tool_call)
            else:
                report.allowed_operations.append(tool_call)

        # Extract security events
        for event in result.events:
            event_str = str(event).lower()
            if (
                "security" in event_str
                or "denied" in event_str
                or "blocked" in event_str
                or "validation failed" in event_str
                or "outside workspace" in event_str
            ):
                report.security_events.append(event)

        return report

    def print_report(
        self,
        result: DebugResult,
        report: AnalysisReport,
        verbose: bool = False,
        security_only: bool = False,
    ) -> None:
        """Print formatted analysis report."""
        console.print()
        console.print(
            Panel(
                f"[bold]Session:[/bold] {result.session_id}\n"
                f"[bold]User:[/bold] {result.user}\n"
                f"[bold]Request:[/bold] {result.request}",
                title="Debug Run",
            )
        )

        # Tool Calls Table
        if result.tool_calls and (not security_only or report.blocked_operations):
            table = Table(title="Tool Calls")
            table.add_column("Tool", style="cyan")
            table.add_column("Input", style="dim", max_width=50)
            table.add_column("Status", style="bold")
            table.add_column("Details", max_width=60)

            for tc in result.tool_calls:
                if security_only and not tc.blocked:
                    continue
                input_json = json.dumps(tc.input, default=str)
                input_str = (
                    input_json[:47] + "..." if len(input_json) > 50 else input_json
                )
                status = "[red]BLOCKED[/red]" if tc.blocked else "[green]OK[/green]"
                details = tc.block_reason or (tc.error or "Success")
                table.add_row(tc.name, input_str, status, str(details)[:60])

            console.print(table)

        # Security Analysis
        if report.blocked_operations:
            console.print()
            console.print("[bold red]Security Blocks:[/bold red]")
            for op in report.blocked_operations:
                input_str = json.dumps(op.input, default=str)[:80]
                console.print(f"  [red]✗[/red] {op.name}({input_str})")
                console.print(f"    Reason: {op.block_reason}")

        # Final message preview
        if result.final_message and not security_only:
            console.print()
            msg_preview = (
                result.final_message[:500] + "..."
                if len(result.final_message) > 500
                else result.final_message
            )
            console.print(Panel(msg_preview, title="Agent Response"))

        # Summary
        console.print()
        console.print(
            Panel(
                f"[bold]Status:[/bold] {result.final_status}\n"
                f"[bold]Duration:[/bold] {result.duration_ms}ms\n"
                f"[bold]Tool Calls:[/bold] {len(result.tool_calls)} "
                f"([green]{len(report.allowed_operations)} allowed[/green], "
                f"[red]{len(report.blocked_operations)} blocked[/red])\n"
                f"[bold]Session Path:[/bold] {result.session_path}",
                title="Summary",
            )
        )

        if result.error:
            console.print(f"\n[red]Error:[/red] {result.error}")

        if verbose:
            console.print()
            console.print("[bold]Raw Events:[/bold]")
            for event in result.events:
                console.print(
                    Syntax(json.dumps(event, indent=2, default=str), "json")
                )

    def dump_session_files(self, session_id: str, user: str | None = None) -> None:
        """Print contents of session files."""
        username = user or self.username or "greg"
        session_path = self.users_dir / username / "sessions" / session_id

        if not session_path.exists():
            console.print(f"[red]Session path not found: {session_path}[/red]")
            return

        console.print()
        console.print(f"[bold]Session Files ({session_path}):[/bold]")

        # List all files
        for f in session_path.rglob("*"):
            if f.is_file():
                rel = f.relative_to(session_path)
                size = f.stat().st_size
                console.print(f"  {rel} ({size} bytes)")

        # Show agent.jsonl
        jsonl_path = session_path / "agent.jsonl"
        if jsonl_path.exists():
            console.print()
            console.print("[bold]agent.jsonl (last 20 lines):[/bold]")
            lines = jsonl_path.read_text().strip().split("\n")
            for line in lines[-20:]:
                try:
                    entry = json.loads(line)
                    entry_type = entry.get("type", "unknown")
                    entry_data = str(entry.get("data", ""))[:80]
                    console.print(f"  {entry_type}: {entry_data}")
                except json.JSONDecodeError:
                    pass


# =============================================================================
# Main
# =============================================================================


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Debug Ag3ntum agent requests with full tracing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -r "Read /etc/passwd" --email user@example.com --password secret
  %(prog)s -r "Write test.txt with hello" --email user@example.com --password secret --verbose
  %(prog)s -r "rm -rf /" --email user@example.com --password secret --security-only
  %(prog)s -r "List files" --email user@example.com --password secret --dump-session
        """,
    )
    parser.add_argument(
        "--request", "-r", required=True, help="Request to send to agent"
    )
    parser.add_argument(
        "--email", "-e", required=True, help="User email for authentication"
    )
    parser.add_argument("--password", default="test123", help="Password")
    parser.add_argument("--host", default="localhost", help="API host")
    parser.add_argument(
        "--port", "-p", type=int, default=40080, help="API port"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show all events"
    )
    parser.add_argument(
        "--security-only",
        "-s",
        action="store_true",
        help="Show only security events",
    )
    parser.add_argument(
        "--dump-session",
        "-d",
        action="store_true",
        help="Dump session files after",
    )

    args = parser.parse_args()

    cli = Ag3ntumDebugCLI(host=args.host, port=args.port)

    console.print(f"\n[bold]Running request:[/bold] {args.request}")
    console.print(f"[dim]Target: {cli.base_url}[/dim]\n")

    result = await cli.run_request(
        args.request, user=args.email, password=args.password
    )

    if result.error and not result.events:
        console.print(f"[red]Error: {result.error}[/red]")
        return 1

    report = cli.analyze_result(result)
    cli.print_report(
        result, report, verbose=args.verbose, security_only=args.security_only
    )

    if args.dump_session and result.session_id:
        cli.dump_session_files(result.session_id)

    # Return exit code based on status
    if result.final_status == "agent_complete":
        return 0
    elif report.blocked_operations:
        return 2  # Security blocks
    else:
        return 1  # Other errors


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
