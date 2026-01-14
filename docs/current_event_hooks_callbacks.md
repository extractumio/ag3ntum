## Complete Hook and Callback Flow for Claude Agent SDK Integration

```
╔══════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                           CLAUDE AGENT SDK MESSAGE FLOW                                              ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════╝

                                    ┌──────────────────────┐
                                    │   Claude Agent SDK   │
                                    │   (claude_agent_sdk) │
                                    └──────────┬───────────┘
                                               │
              ┌────────────────────────────────┼────────────────────────────────┐
              │                                │                                │
              ▼                                ▼                                ▼
    ┌─────────────────────┐      ┌─────────────────────────┐      ┌─────────────────────────┐
    │   SDK Hook Events   │      │   Legacy Callbacks      │      │  Streaming Messages     │
    │   (HookMatcher)     │      │   (ClaudeAgentOptions)  │      │  (receive_response)     │
    └──────────┬──────────┘      └───────────┬─────────────┘      └───────────┬─────────────┘
               │                             │                                │
               │                             │                                │
╔══════════════▼══════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                                      ║
║  ┌────────────────────────────────────────────────────────────────────────────────────────────────┐  ║
║  │                              SDK HOOK EVENTS (hooks.py)                                        │  ║
║  │                                                                                                │  ║
║  │   HooksManager class manages 5 hook event types:                                              │  ║
║  │                                                                                                │  ║
║  │   ┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────────────────────┐ │  ║
║  │   │    PreToolUse       │   │    PostToolUse      │   │       UserPromptSubmit              │ │  ║
║  │   │                     │   │                     │   │                                     │ │  ║
║  │   │ BEFORE tool runs    │   │ AFTER tool runs     │   │ User prompt submitted               │ │  ║
║  │   │                     │   │                     │   │                                     │ │  ║
║  │   │ Input:              │   │ Input:              │   │ Input:                              │ │  ║
║  │   │  • tool_name        │   │  • tool_name        │   │  • prompt                           │ │  ║
║  │   │  • tool_input       │   │  • tool_result      │   │                                     │ │  ║
║  │   │  • tool_use_id      │   │  • is_error         │   │ Output:                             │ │  ║
║  │   │                     │   │                     │   │  • updatedPrompt                    │ │  ║
║  │   │ Output:             │   │ Output:             │   │                                     │ │  ║
║  │   │  • permissionDec.   │   │  • (none)           │   └─────────────────────────────────────┘ │  ║
║  │   │  • updatedInput     │   │                     │                                           │  ║
║  │   │  • interrupt        │   │                     │   ┌─────────────────────────────────────┐ │  ║
║  │   │  • systemMessage    │   │                     │   │           Stop                      │ │  ║
║  │   └─────────────────────┘   └─────────────────────┘   │                                     │ │  ║
║  │            │                          │               │ Agent stops execution               │ │  ║
║  │            │                          │               │                                     │ │  ║
║  │            ▼                          ▼               │ Input: stop_hook_active             │ │  ║
║  │   ┌─────────────────────────────────────────────┐    └─────────────────────────────────────┘ │  ║
║  │   │          Hook Factory Functions              │                                           │  ║
║  │   │                                              │    ┌─────────────────────────────────────┐ │  ║
║  │   │  PreToolUse Hooks:                           │    │        SubagentStop                 │ │  ║
║  │   │  ├─ create_permission_hook()                 │    │                                     │ │  ║
║  │   │  │    • Permission checking                  │    │ Subagent completes                  │ │  ║
║  │   │  │    • Smart interrupt after N denials      │    │                                     │ │  ║
║  │   │  │    • Actionable denial messages           │    │ Input:                              │ │  ║
║  │   │  │                                           │    │  • subagent_type                    │ │  ║
║  │   │  ├─ create_dangerous_command_hook()          │    │  • result                           │ │  ║
║  │   │  │    • Block dangerous Bash patterns        │    └─────────────────────────────────────┘ │  ║
║  │   │  │    • Loads from config/security/          │                                           │  ║
║  │   │  │                                           │                                           │  ║
║  │   │  ├─ create_absolute_path_block_hook()        │                                           │  ║
║  │   │  │    • Block absolute paths                 │                                           │  ║
║  │   │  │    • Block parent traversal (..)          │                                           │  ║
║  │   │  │                                           │                                           │  ║
║  │   │  ├─ create_sandbox_execution_hook()          │                                           │  ║
║  │   │  │    • Wrap Bash in bubblewrap sandbox      │                                           │  ║
║  │   │  │    • Filesystem isolation                 │                                           │  ║
║  │   │  │                                           │                                           │  ║
║  │   │  └─ create_path_normalization_hook()         │                                           │  ║
║  │   │       • Convert absolute → relative paths    │                                           │  ║
║  │   │                                              │                                           │  ║
║  │   │  PostToolUse Hooks:                          │                                           │  ║
║  │   │  └─ create_audit_hook()                      │                                           │  ║
║  │   │       • Log tool usage to file               │                                           │  ║
║  │   │       • on_tool_complete callback            │                                           │  ║
║  │   │                                              │                                           │  ║
║  │   │  UserPromptSubmit Hooks:                     │                                           │  ║
║  │   │  └─ create_prompt_enhancement_hook()         │                                           │  ║
║  │   │       • Add timestamp to prompts             │                                           │  ║
║  │   │       • Add context injection                │                                           │  ║
║  │   │                                              │                                           │  ║
║  │   │  Stop Hooks:                                 │                                           │  ║
║  │   │  └─ create_stop_hook()                       │                                           │  ║
║  │   │       • Session cleanup                      │                                           │  ║
║  │   │       • on_stop callback                     │                                           │  ║
║  │   │       • cleanup_fn async                     │                                           │  ║
║  │   │                                              │                                           │  ║
║  │   │  SubagentStop Hooks:                         │                                           │  ║
║  │   │  └─ create_subagent_stop_hook()              │                                           │  ║
║  │   │       • on_subagent_complete callback        │                                           │  ║
║  │   └──────────────────────────────────────────────┘                                           │  ║
║  └────────────────────────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                                      ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                                      ║
║  ┌────────────────────────────────────────────────────────────────────────────────────────────────┐  ║
║  │                       LEGACY CALLBACKS (permissions.py)                                        │  ║
║  │                                                                                                │  ║
║  │   ClaudeAgentOptions.can_use_tool                                                             │  ║
║  │                                                                                                │  ║
║  │   ┌──────────────────────────────────────────────────────────────────────────────────────┐    │  ║
║  │   │  create_permission_callback()                                                         │    │  ║
║  │   │                                                                                       │    │  ║
║  │   │  Parameters:                                                                          │    │  ║
║  │   │   • permission_manager        - Rule-based permission checker                        │    │  ║
║  │   │   • on_permission_check       - Callback(tool_name, decision) for tracing            │    │  ║
║  │   │   • denial_tracker            - PermissionDenialTracker for recording denials        │    │  ║
║  │   │   • trace_processor           - TraceProcessor for status updates                    │    │  ║
║  │   │   • max_denials_before_intr.  - Smart interrupt threshold (default: 3)               │    │  ║
║  │   │   • system_message_builder    - Custom system message generator                      │    │  ║
║  │   │   • sandbox_executor          - SandboxExecutor for Bash wrapping                    │    │  ║
║  │   │                                                                                       │    │  ║
║  │   │  Returns:                                                                             │    │  ║
║  │   │   async (tool_name, tool_input) → {"allow": bool, "message": str, ...}               │    │  ║
║  │   └──────────────────────────────────────────────────────────────────────────────────────┘    │  ║
║  │                                                                                                │  ║
║  │   ┌──────────────────────────────────────────────────────────────────────────────────────┐    │  ║
║  │   │  create_permission_hooks()                                                            │    │  ║
║  │   │                                                                                       │    │  ║
║  │   │  Modern replacement - builds HooksManager with:                                       │    │  ║
║  │   │   1. create_absolute_path_block_hook()   (first - enforce relative paths)            │    │  ║
║  │   │   2. create_permission_hook()            (permission checking)                       │    │  ║
║  │   │   3. create_dangerous_command_hook()     (Bash patterns, matcher="Bash")             │    │  ║
║  │   │                                                                                       │    │  ║
║  │   │  Returns: dict[str, list[HookMatcher]] for ClaudeAgentOptions.hooks                  │    │  ║
║  │   └──────────────────────────────────────────────────────────────────────────────────────┘    │  ║
║  │                                                                                                │  ║
║  │   ClaudeAgentOptions.stderr                                                                   │  ║
║  │                                                                                                │  ║
║  │   ┌──────────────────────────────────────────────────────────────────────────────────────┐    │  ║
║  │   │  create_stderr_callback(tracer)                                                       │    │  ║
║  │   │                                                                                       │    │  ║
║  │   │  Traces CLI stderr output to tracer.on_error()                                       │    │  ║
║  │   └──────────────────────────────────────────────────────────────────────────────────────┘    │  ║
║  └────────────────────────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                                      ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                                      ║
║  ┌────────────────────────────────────────────────────────────────────────────────────────────────┐  ║
║  │                    MESSAGE PROCESSING (trace_processor.py)                                     │  ║
║  │                                                                                                │  ║
║  │   TraceProcessor.process_message(message)                                                     │  ║
║  │                                                                                                │  ║
║  │   SDK Message Types Handled:                                                                  │  ║
║  │   ┌──────────────────────────────────────────────────────────────────────────────────────┐    │  ║
║  │   │  SystemMessage           →  _handle_system_message()                                  │    │  ║
║  │   │   • subtype: "init"      →  tracer.on_agent_start(session_id, model, tools, cwd)     │    │  ║
║  │   │   • subtype: "error"     →  tracer.on_error(message, error_type)                     │    │  ║
║  │   │   • subtype: other       →  tracer.on_system_event(subtype, data)                    │    │  ║
║  │   └──────────────────────────────────────────────────────────────────────────────────────┘    │  ║
║  │   ┌──────────────────────────────────────────────────────────────────────────────────────┐    │  ║
║  │   │  AssistantMessage        →  _handle_assistant_message()                               │    │  ║
║  │   │   • TextBlock            →  tracer.on_message(text)                                  │    │  ║
║  │   │   • ThinkingBlock        →  tracer.on_thinking(thinking)                             │    │  ║
║  │   │   • ToolUseBlock         →  tracer.on_tool_start(name, input, id)                    │    │  ║
║  │   │                             + tracer.on_subagent_start() if Task tool                │    │  ║
║  │   │   • ToolResultBlock      →  tracer.on_tool_complete(name, id, result, ms, error)     │    │  ║
║  │   │                             + tracer.on_subagent_stop() if Task tool                 │    │  ║
║  │   └──────────────────────────────────────────────────────────────────────────────────────┘    │  ║
║  │   ┌──────────────────────────────────────────────────────────────────────────────────────┐    │  ║
║  │   │  UserMessage             →  _handle_user_message()                                    │    │  ║
║  │   │   • (if include_user_messages)  →  tracer.on_message("[USER] " + content)            │    │  ║
║  │   └──────────────────────────────────────────────────────────────────────────────────────┘    │  ║
║  │   ┌──────────────────────────────────────────────────────────────────────────────────────┐    │  ║
║  │   │  StreamEvent             →  _handle_stream_event()                                    │    │  ║
║  │   │   • message_start        →  extract usage metrics                                    │    │  ║
║  │   │   • message_delta        →  update usage metrics                                     │    │  ║
║  │   │   • message_stop         →  tracer.on_message("", is_partial=False)                  │    │  ║
║  │   │                             or tracer.on_subagent_message() if in subagent           │    │  ║
║  │   │   • content_block_start  →  tracer.on_message(text, is_partial=True)                 │    │  ║
║  │   │   • content_block_delta  →  tracer.on_message(text, is_partial=True)                 │    │  ║
║  │   │                                                                                       │    │  ║
║  │   │   Usage updates          →  tracer.on_metrics_update(payload)                        │    │  ║
║  │   └──────────────────────────────────────────────────────────────────────────────────────┘    │  ║
║  │   ┌──────────────────────────────────────────────────────────────────────────────────────┐    │  ║
║  │   │  ResultMessage           →  _handle_result_message()                                  │    │  ║
║  │   │   • (currently no-op, metrics extracted from StreamEvent)                            │    │  ║
║  │   └──────────────────────────────────────────────────────────────────────────────────────┘    │  ║
║  │                                                                                                │  ║
║  │   create_trace_hooks(tracer)  - SDK hooks for tracing:                                       │  ║
║  │   ┌──────────────────────────────────────────────────────────────────────────────────────┐    │  ║
║  │   │  PreToolUse  → tracer.on_tool_start(tool_name, tool_input, session_id)               │    │  ║
║  │   │  PostToolUse → tracer.on_tool_complete(tool_name, session_id, result, 0, is_error)   │    │  ║
║  │   │  Stop        → tracer.on_system_event("stop", {active: bool})                        │    │  ║
║  │   └──────────────────────────────────────────────────────────────────────────────────────┘    │  ║
║  └────────────────────────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                                      ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                                      ║
║  ┌────────────────────────────────────────────────────────────────────────────────────────────────┐  ║
║  │                         TRACER CALLBACK INTERFACE (tracer.py)                                  │  ║
║  │                                                                                                │  ║
║  │   TracerBase (Abstract Base Class) - All tracers implement these:                             │  ║
║  │                                                                                                │  ║
║  │   ┌────────────────────────────────────────────────────────────────────────────────────────┐  │  ║
║  │   │  AGENT LIFECYCLE                                                                       │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_agent_start(session_id, model, tools, working_dir, skills?, task?)                │  │  ║
║  │   │      └── Called when agent starts execution                                            │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_agent_complete(status, num_turns, duration_ms, total_cost_usd, result,            │  │  ║
║  │   │                    session_id?, usage?, model?, cumulative_cost?, turns?, tokens?)    │  │  ║
║  │   │      └── Called when agent completes (COMPLETE, PARTIAL, FAILED)                      │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_output_display(output?, error?, comments?, result_files?, status?)                │  │  ║
║  │   │      └── Called after structured result is parsed                                      │  │  ║
║  │   └────────────────────────────────────────────────────────────────────────────────────────┘  │  ║
║  │                                                                                                │  ║
║  │   ┌────────────────────────────────────────────────────────────────────────────────────────┐  │  ║
║  │   │  TOOL EXECUTION                                                                        │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_tool_start(tool_name, tool_input, tool_id)                                        │  │  ║
║  │   │      └── Called before tool/skill executes                                             │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_tool_complete(tool_name, tool_id, result, duration_ms, is_error)                  │  │  ║
║  │   │      └── Called after tool/skill completes                                             │  │  ║
║  │   └────────────────────────────────────────────────────────────────────────────────────────┘  │  ║
║  │                                                                                                │  ║
║  │   ┌────────────────────────────────────────────────────────────────────────────────────────┐  │  ║
║  │   │  CONTENT STREAMING                                                                     │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_message(text, is_partial=False)                                                   │  │  ║
║  │   │      └── Called for assistant text output (streaming or complete)                     │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_thinking(thinking_text)                                                           │  │  ║
║  │   │      └── Called for thinking/reasoning blocks                                          │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_error(error_message, error_type="error")                                          │  │  ║
║  │   │      └── Called when any error occurs                                                  │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_metrics_update(metrics)                                                           │  │  ║
║  │   │      └── Called when token/cost metrics are updated                                    │  │  ║
║  │   └────────────────────────────────────────────────────────────────────────────────────────┘  │  ║
║  │                                                                                                │  ║
║  │   ┌────────────────────────────────────────────────────────────────────────────────────────┐  │  ║
║  │   │  PERMISSION & HOOKS                                                                    │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_profile_switch(profile_type, profile_name, tools, allow_count, deny_count, path?) │  │  ║
║  │   │      └── Called when permission profile changes                                        │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_hook_triggered(hook_event, tool_name?, decision?, message?)                       │  │  ║
║  │   │      └── Called when SDK hook fires (PreToolUse, PostToolUse, etc.)                   │  │  ║
║  │   └────────────────────────────────────────────────────────────────────────────────────────┘  │  ║
║  │                                                                                                │  ║
║  │   ┌────────────────────────────────────────────────────────────────────────────────────────┐  │  ║
║  │   │  MULTI-TURN CONVERSATION                                                               │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_conversation_turn(turn_number, prompt_preview, response_preview, duration, tools) │  │  ║
║  │   │      └── Called when conversation turn completes                                       │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_session_connect(session_id?)                                                      │  │  ║
║  │   │      └── Called when conversation session connects                                     │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_session_disconnect(session_id?, total_turns, total_duration_ms)                   │  │  ║
║  │   │      └── Called when conversation session disconnects                                  │  │  ║
║  │   └────────────────────────────────────────────────────────────────────────────────────────┘  │  ║
║  │                                                                                                │  ║
║  │   ┌────────────────────────────────────────────────────────────────────────────────────────┐  │  ║
║  │   │  SUBAGENT (Task tool)                                                                  │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_subagent_start(task_id, subagent_name, prompt)                                    │  │  ║
║  │   │      └── Called when Task tool invokes a subagent                                      │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_subagent_message(task_id, text, is_partial=False)                                 │  │  ║
║  │   │      └── Called for messages within subagent context                                   │  │  ║
║  │   │                                                                                        │  │  ║
║  │   │  on_subagent_stop(task_id, result, duration_ms, is_error)                             │  │  ║
║  │   │      └── Called when subagent completes                                                │  │  ║
║  │   └────────────────────────────────────────────────────────────────────────────────────────┘  │  ║
║  │                                                                                                │  ║
║  │   Implementations:                                                                            │  ║
║  │   • ExecutionTracer       - Rich console output with spinners, colors, boxes                 │  ║
║  │   • BackendConsoleTracer  - Simple logging for backend use                                   │  ║
║  │   • EventingTracer        - Wraps tracer + emits events to EventQueue                        │  ║
║  │   • JsonlTracer           - Writes JSONL log file                                            │  ║
║  │   • NoOpTracer            - Silent/null tracer for testing                                   │  ║
║  └────────────────────────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                                      ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                                      ║
║  ┌────────────────────────────────────────────────────────────────────────────────────────────────┐  ║
║  │                     CONVERSATION SESSION CALLBACKS (conversation.py)                           │  ║
║  │                                                                                                │  ║
║  │   ConversationSession constructor accepts these optional callbacks:                           │  ║
║  │                                                                                                │  ║
║  │   ┌──────────────────────────────────────────────────────────────────────────────────────┐    │  ║
║  │   │  on_message: Callable[[Any], None]                                                    │    │  ║
║  │   │      └── Called for each SDK message received                                         │    │  ║
║  │   │                                                                                       │    │  ║
║  │   │  on_tool_start: Callable[[str, dict, str], None]                                     │    │  ║
║  │   │      └── Called when tool starts: (tool_name, tool_input, tool_id)                   │    │  ║
║  │   │                                                                                       │    │  ║
║  │   │  on_tool_complete: Callable[[str, str, Any, bool], None]                             │    │  ║
║  │   │      └── Called when tool ends: (tool_name, tool_id, result, is_error)               │    │  ║
║  │   │                                                                                       │    │  ║
║  │   │  on_turn_complete: Callable[[ConversationTurn], None]                                │    │  ║
║  │   │      └── Called when conversation turn completes                                      │    │  ║
║  │   └──────────────────────────────────────────────────────────────────────────────────────┘    │  ║
║  └────────────────────────────────────────────────────────────────────────────────────────────────┘  ║
║                                                                                                      ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                            DATA CLASSES & TYPES                                                      ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                                      ║
║  HookCallback = Callable[[dict[str, Any], Optional[str], Any], Awaitable[dict[str, Any]]]          ║
║      └── Signature: async (input_data, tool_use_id, context) -> response_dict                       ║
║                                                                                                      ║
║  HookResult (dataclass)                                                                              ║
║      ├── permission_decision: Optional[str]  ("allow", "deny", "ask")                               ║
║      ├── permission_reason: Optional[str]                                                           ║
║      ├── updated_input: Optional[dict]                                                              ║
║      ├── block: bool                                                                                 ║
║      ├── interrupt: bool                                                                             ║
║      ├── system_message: Optional[str]                                                              ║
║      └── hook_output: Optional[dict]                                                                ║
║          └── .to_sdk_response(hook_event) → SDK-compatible dict                                     ║
║                                                                                                      ║
║  ToolUsageRecord (dataclass)                                                                         ║
║      ├── tool_name, tool_id, input_data                                                             ║
║      ├── timestamp, duration_ms, result                                                             ║
║      └── is_error, permission_decision                                                               ║
║                                                                                                      ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                         COMPLETE HOOK CHAIN EXECUTION ORDER                                          ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                                      ║
║  User Prompt Submitted:                                                                              ║
║      UserPromptSubmit hooks → [create_prompt_enhancement_hook()]                                    ║
║                                                                                                      ║
║  Tool Execution Request:                                                                             ║
║      1. PreToolUse hooks (in registration order):                                                   ║
║         ├── create_absolute_path_block_hook()    [FIRST - blocks bad paths]                        ║
║         ├── create_permission_hook()             [permission checking]                              ║
║         ├── create_dangerous_command_hook()      [Bash patterns, matcher="Bash"]                   ║
║         ├── create_sandbox_execution_hook()      [wrap Bash in bwrap]                              ║
║         └── create_path_normalization_hook()     [abs → rel conversion]                            ║
║                                                                                                      ║
║      2. OR: can_use_tool callback (legacy)                                                          ║
║         └── create_permission_callback()                                                            ║
║                                                                                                      ║
║      3. [Tool Executes]                                                                              ║
║                                                                                                      ║
║      4. PostToolUse hooks:                                                                           ║
║         └── create_audit_hook()                  [logging]                                          ║
║                                                                                                      ║
║  Agent Stops:                                                                                        ║
║      Stop hooks → [create_stop_hook()]                                                              ║
║                                                                                                      ║
║  Subagent Completes:                                                                                 ║
║      SubagentStop hooks → [create_subagent_stop_hook()]                                             ║
║                                                                                                      ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════╝
```

---

## Quick Reference Table

| Category | Hook/Callback | File | Purpose |
|----------|--------------|------|---------|
| **SDK PreToolUse** | `create_permission_hook()` | `hooks.py` | Permission checking with smart interrupt |
| **SDK PreToolUse** | `create_dangerous_command_hook()` | `hooks.py` | Block dangerous Bash commands |
| **SDK PreToolUse** | `create_absolute_path_block_hook()` | `hooks.py` | Block absolute/traversal paths |
| **SDK PreToolUse** | `create_sandbox_execution_hook()` | `hooks.py` | Wrap Bash in bubblewrap sandbox |
| **SDK PreToolUse** | `create_path_normalization_hook()` | `hooks.py` | Convert absolute → relative paths |
| **SDK PostToolUse** | `create_audit_hook()` | `hooks.py` | Audit logging to file |
| **SDK UserPromptSubmit** | `create_prompt_enhancement_hook()` | `hooks.py` | Add timestamp/context to prompts |
| **SDK Stop** | `create_stop_hook()` | `hooks.py` | Session cleanup |
| **SDK SubagentStop** | `create_subagent_stop_hook()` | `hooks.py` | Process subagent results |
| **Legacy** | `create_permission_callback()` | `permissions.py` | `can_use_tool` callback |
| **Legacy** | `create_permission_hooks()` | `permissions.py` | Combined hooks config builder |
| **Trace** | `create_trace_hooks()` | `trace_processor.py` | SDK hooks for tracing |
| **Trace** | `create_stderr_callback()` | `trace_processor.py` | stderr → tracer.on_error() |
| **Tracer** | `on_agent_start()` | `tracer.py` | Agent lifecycle start |
| **Tracer** | `on_agent_complete()` | `tracer.py` | Agent lifecycle end |
| **Tracer** | `on_tool_start()` | `tracer.py` | Tool execution start |
| **Tracer** | `on_tool_complete()` | `tracer.py` | Tool execution end |
| **Tracer** | `on_message()` | `tracer.py` | Text output (streaming/complete) |
| **Tracer** | `on_thinking()` | `tracer.py` | Thinking/reasoning blocks |
| **Tracer** | `on_error()` | `tracer.py` | Error handling |
| **Tracer** | `on_metrics_update()` | `tracer.py` | Token/cost metrics |
| **Tracer** | `on_profile_switch()` | `tracer.py` | Permission profile changes |
| **Tracer** | `on_hook_triggered()` | `tracer.py` | Hook event tracing |
| **Tracer** | `on_conversation_turn()` | `tracer.py` | Multi-turn conversation |
| **Tracer** | `on_session_connect()` | `tracer.py` | Session connection |
| **Tracer** | `on_session_disconnect()` | `tracer.py` | Session disconnection |
| **Tracer** | `on_subagent_start()` | `tracer.py` | Task tool subagent start |
| **Tracer** | `on_subagent_message()` | `tracer.py` | Subagent streaming |
| **Tracer** | `on_subagent_stop()` | `tracer.py` | Subagent completion |
| **Tracer** | `on_output_display()` | `tracer.py` | Structured result display |
| **Conversation** | `on_message` | `conversation.py` | Each SDK message |
| **Conversation** | `on_tool_start` | `conversation.py` | Tool start |
| **Conversation** | `on_tool_complete` | `conversation.py` | Tool end |
| **Conversation** | `on_turn_complete` | `conversation.py` | Turn completion |