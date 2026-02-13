<!--
name: 'System Reminder: Hook Success'
description: Notification when a hook completes successfully
version: 1.0.0
variables:
  - HOOK_NAME
  - HOOK_OUTPUT
override_allowed: false
-->

Hook `${HOOK_NAME}` completed successfully.

Output:
${HOOK_OUTPUT}
