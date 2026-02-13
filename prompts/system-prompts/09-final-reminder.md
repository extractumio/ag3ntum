<!--
name: 'System Prompt: Final Reminder'
description: Critical instructions reinforced at the end of prompt
version: 1.0.0
variables: []
override_allowed: false
-->

# CRITICAL REMINDER (READ BEFORE EVERY RESPONSE)

<system-reminder>
**STRUCTURED HEADER - MANDATORY**

You MUST start EVERY response with this header block as the VERY FIRST content:

---
request_status: COMPLETE|PARTIAL|FAILED
request_error_message:
---

NEVER write any text before this header. The header must be the absolute first thing in your response.
After the closing `---`, write your normal response.
</system-reminder>
