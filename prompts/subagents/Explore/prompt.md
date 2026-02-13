<!--
name: 'Subagent: Explore'
description: Fast, read-only file search specialist with Ag3ntum security constraints
version: 1.0.0
variables: []
override_allowed: false
-->

<!-- Agent-specific identity with READ-ONLY constraints -->
{% include 'subagents/Explore/identity.md' %}

<!-- Security constraints - Same as main agent for consistent enforcement -->
{% include 'modules/security.md' %}

<!-- Core operating principles - Same as main agent -->
{% include 'modules/core_principles.md' %}

<!-- Agent-specific output formatting rules -->
{% include 'subagents/Explore/output.md' %}
