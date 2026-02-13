<!--
name: 'System Reminder: File Modified'
description: Notification when file was modified externally
version: 1.0.0
variables:
  - FILE_PATH
override_allowed: false
-->

The file `${FILE_PATH}` was modified by the user or an external tool since you last read it.

You should re-read the file before making further changes to avoid overwriting the user's edits.
