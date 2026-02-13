<!--
name: 'Subagent: general-purpose'
description: Security-hardened general-purpose research agent
version: 1.0.0
variables:
  - ENABLE_SKILLS
override_allowed: false
-->

<!-- Agent-specific identity -->
{% include 'subagents/general-purpose/identity.md' %}

<!-- Security constraints - Same as main agent for consistent enforcement -->
{% include 'modules/security.md' %}

<!-- Core operating principles - Same as main agent -->
{% include 'modules/core_principles.md' %}

<!-- Tool usage policy - Parallel execution, subagent delegation rules -->
{% include 'modules/tools.md' %}

{% if ENABLE_SKILLS %}
<!-- Skills integration - SDK-native skill discovery -->
{% include 'modules/skills.md' %}
{% endif %}

<!-- Agent-specific output formatting rules -->
{% include 'subagents/general-purpose/output.md' %}
