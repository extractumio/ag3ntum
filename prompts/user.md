<!--
name: 'User Prompt'
description: Task execution prompt with optional context
version: 1.0.0
variables:
  - TASK
  - HAS_CONTEXT
  - CONTEXT
override_allowed: true
-->

<task>
${TASK}
</task>

${HAS_CONTEXT?<context>
${CONTEXT}
</context>
:}
Execute the task according to your instructions. For the response:

1. Begin with the required structured header (status, error).
2. Stream your response as you produce it.
3. If you create files, mention their relative paths.
4. If you need clarification, use the mcp__ag3ntum__AskUserQuestion tool.
5. Track complex tasks with TodoWrite.
