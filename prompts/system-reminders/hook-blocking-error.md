<!--
name: 'System Reminder: Hook Blocking Error'
description: Notification when a hook blocks execution due to an error
version: 1.0.0
variables:
  - HOOK_NAME
  - HOOK_ERROR
override_allowed: false
-->

Hook `${HOOK_NAME}` blocked execution.

Error:
${HOOK_ERROR}
