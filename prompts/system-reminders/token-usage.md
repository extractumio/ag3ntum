<!--
name: 'System Reminder: Token Usage'
description: Token usage status notification
version: 1.0.0
variables:
  - TOKENS_USED
  - TOKENS_TOTAL
  - TOKENS_REMAINING
override_allowed: false
-->

Token usage: ${TOKENS_USED} / ${TOKENS_TOTAL} (${TOKENS_REMAINING} remaining)
