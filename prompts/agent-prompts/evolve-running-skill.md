<!--
name: 'Agent Prompt: Evolve Currently Running Skill'
description: Detect user feedback to permanently update skill definitions
version: 1.0.0
variables:
  - SKILL_DEFINITION
  - RECENT_MESSAGES
override_allowed: false
-->

Analyze the recent conversation during skill execution to identify user preferences and corrections that should be permanently incorporated into the skill definition.

Look for:
- Requests to add, change, or remove steps
- Preferences about how steps should work
- Corrections ("no, do X instead", "always use Y")
- Feedback on output format or quality

Current skill definition:
${SKILL_DEFINITION}

Recent conversation messages:
${RECENT_MESSAGES}

Output a JSON array of updates, each with:
- "section": which part of the skill to update
- "change": what to change (add/modify/remove)
- "reason": why this change should be permanent

Wrap your output in <updates></updates> tags.

Only suggest changes that represent genuine user preferences, not one-time adjustments. If there are no permanent updates to suggest, output an empty array.
