<!--
name: 'System Reminder: Lines Selected in IDE'
description: Notification when specific lines are selected in the IDE
version: 1.0.0
variables:
  - SELECTED_LINES
  - FILE_PATH
override_allowed: false
-->

Lines ${SELECTED_LINES} are currently selected in `${FILE_PATH}` in the IDE.
