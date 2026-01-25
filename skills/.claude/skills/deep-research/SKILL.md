---
name: deep-research
description: |
  Use when the user asks to research, investigate, explore, or analyze a topic requiring multiple
  sources. Triggers: "research X", "find out about", "what's the current state of", "compare options
  for", "deep dive into", "gather information on", "write a report about". NOT for simple factual
  questions answerable in one search—only for multi-source synthesis needing 5+ sources.
---
# Deep Research Agent Skill

## Overview

This skill enables Claude to conduct exhaustive, graduate-level research on complex topics using an agentic, multi-phase approach. It transforms Claude from a passive responder into an autonomous research architect capable of planning investigations, executing recursive searches, resolving conflicts, and synthesizing comprehensive reports.

## When to Use This Skill

- Complex research queries requiring multi-source synthesis
- Topics needing exploration from multiple perspectives
- Requests for comprehensive reports, analyses, or literature reviews
- Questions with "unknown unknowns" requiring iterative discovery
- Tasks requiring fact verification and conflict resolution

---

## Tool Reference Quick Guide

| Task | Tool | Notes |
|------|------|-------|
| Web search | `WebSearch` | Primary discovery tool |
| Fetch webpage content | `mcp__ag3ntum__WebFetch` | Extract specific data from URLs |
| Ask user for clarification | `AskUserQuestion` | Use structured options when possible |
| Track research progress | `TodoWrite` | **Required** - maintain throughout |
| Parallel research threads | `Task` | Launch concurrent subagents |
| Save intermediate results | `mcp__ag3ntum__Write` | Preserve findings to files |
| Read saved research | `mcp__ag3ntum__Read` | Resume from checkpoints |
| Search local files | `mcp__ag3ntum__Grep` | Find in accumulated research |

---

## Core Architecture: The Cognitive Triad

### 1. Planner Role
- Decomposes complex queries into hierarchical, searchable sub-questions
- Generates structured research plans with clear dependencies
- Identifies diverse perspectives to avoid filter bubbles
- Creates a Directed Acyclic Graph (DAG) of research tasks

### 2. Executor Role
- Executes search queries and retrieves content
- Applies Chain of Density summarization to compress findings
- Maintains isolation between parallel research threads
- Tracks sources with full citation metadata

### 3. Critic Role
- Validates findings against original requirements
- Detects contradictions and triggers verification
- Enforces quality standards and citation fidelity
- Triggers re-search loops when information is insufficient

---

## Task Management with TodoWrite

**CRITICAL**: Use `TodoWrite` to track all research phases and provide visibility into progress.

### Initial Setup (Immediately After Receiving Request)

```
TodoWrite with todos:
1. "Clarify research requirements with user" - status: in_progress
2. "Create research plan with perspectives" - status: pending
3. "Execute research for Section 1: [topic]" - status: pending
4. "Execute research for Section 2: [topic]" - status: pending
5. "Resolve conflicts and verify sources" - status: pending
6. "Synthesize final report" - status: pending
```

### TodoWrite Rules for Research

1. **One task `in_progress` at a time** - Mark current phase as active
2. **Mark completed immediately** - Don't batch completions
3. **Add discovered tasks dynamically** - If research reveals new sections, add them
4. **Use descriptive names** - Include section topics in task names
5. **Track parallel threads** - Each Task subagent should have its own todo item

### Dynamic Task Addition Example

When Phase 2 planning identifies 4 research sections:
```
TodoWrite - add new todos:
- "Research: Economic Impact Analysis" - pending
- "Research: Technical Implementation" - pending
- "Research: Policy & Regulation" - pending
- "Research: Case Studies & Examples" - pending
```

---

## Parallelization with Task Tool

Use the `Task` tool to launch concurrent research threads for **independent** sections.

### When to Parallelize

| Scenario | Parallelize? | Reason |
|----------|--------------|--------|
| Multiple independent sections | ✅ Yes | No dependencies between sections |
| Fact verification across sources | ✅ Yes | Sources don't depend on each other |
| Sequential discoveries | ❌ No | Later searches depend on earlier findings |
| Conflict resolution | ❌ No | Need all facts before resolving |
| Final synthesis | ❌ No | Requires all research complete |

### Parallel Research Pattern

Launch multiple research threads in a **single message** with multiple Task tool calls:

```
// In ONE message, call Task multiple times:

Task 1:
  subagent_type: "general-purpose"
  description: "Research economic impact"
  prompt: |
    Research the economic impact of [topic]. Focus on:
    - Market size and growth projections
    - Cost-benefit analyses
    - Economic stakeholder perspectives

    Save findings to: workspace/research/economic_impact.md
    Include all source URLs with citations.

Task 2:
  subagent_type: "general-purpose"
  description: "Research technical aspects"
  prompt: |
    Research the technical implementation of [topic]. Focus on:
    - Current technologies and approaches
    - Technical challenges and limitations
    - Expert technical opinions

    Save findings to: workspace/research/technical_aspects.md
    Include all source URLs with citations.

Task 3:
  subagent_type: "general-purpose"
  description: "Research policy landscape"
  prompt: |
    Research policy and regulation for [topic]. Focus on:
    - Current regulatory frameworks
    - Proposed legislation
    - International comparisons

    Save findings to: workspace/research/policy_landscape.md
    Include all source URLs with citations.
```

### Subagent Prompt Template

When launching research subagents, include:

```markdown
## Research Task: [Section Title]

### Objective
[Specific research goal from the plan]

### Questions to Answer
1. [Sub-question 1]
2. [Sub-question 2]
3. [Sub-question 3]

### Search Strategy
- Start with: [broad query]
- Then narrow to: [specific queries]
- Verify with: [authoritative source types]

### Output Requirements
- Save to: workspace/research/[section_name].md
- Format: Structured markdown with headers
- Citations: Include URL, title, date for every claim
- Confidence: Note uncertainty levels

### Tools Available
- WebSearch: For discovering sources
- mcp__ag3ntum__WebFetch: For extracting content from URLs
- mcp__ag3ntum__Write: For saving intermediate findings
```

### Collecting Parallel Results

After launching parallel tasks:

1. **Wait for completion** - Task tool returns when subagent finishes
2. **Read saved files** - Use `mcp__ag3ntum__Read` to load each section's findings
3. **Update TodoWrite** - Mark each section as completed
4. **Proceed to synthesis** - Only after ALL parallel tasks complete

```
// After parallel tasks complete:
mcp__ag3ntum__Read workspace/research/economic_impact.md
mcp__ag3ntum__Read workspace/research/technical_aspects.md
mcp__ag3ntum__Read workspace/research/policy_landscape.md

// Then synthesize in the main agent context
```

---

## State Management

Maintain a mental model of research state throughout the process:
```json
{
  "task": "Original user query",
  "research_brief": "Clarified goals, scope, and constraints",
  "plan": {
    "sections": [
      {
        "id": 1,
        "topic": "Section topic",
        "status": "pending|in_progress|complete",
        "sub_questions": ["Question 1", "Question 2"],
        "knowledge_gaps": []
      }
    ]
  },
  "knowledge_graph": [
    {
      "fact": "Specific finding",
      "source": "URL or citation",
      "confidence": 0.9,
      "perspective": "economic|technical|policy|etc"
    }
  ],
  "conflicts": [
    {
      "claim": "Disputed fact",
      "source_a": {"position": "X", "url": "..."},
      "source_b": {"position": "Y", "url": "..."},
      "resolution": "pending|resolved|irreconcilable"
    }
  ],
  "reflexion_log": ["Observation 1", "Adjustment 2"]
}
```

## Research Workflow

### Phase 1: Contextual Scoping

Before researching, analyze the query for clarity and scope.

**Tools for Phase 1:**
| Action | Tool | Purpose |
|--------|------|---------|
| Clarify requirements | `AskUserQuestion` | Structured user input |
| Initial topic discovery | `WebSearch` | Understand landscape |
| Record research brief | `mcp__ag3ntum__Write` | Save to workspace/research_brief.md |
| Track progress | `TodoWrite` | Mark scoping as in_progress |

**Actions:**
1. **Mark TodoWrite** - Set "Clarify research requirements" to `in_progress`
2. Identify the core intent behind the request
3. Detect ambiguities requiring clarification
4. Determine appropriate depth, breadth, and time horizon (defaults shall include the latest information available, so consider the current year or current date as default)
5. Generate a Research Brief
6. **Mark TodoWrite** - Set "Clarify research requirements" to `completed`

#### Using AskUserQuestion for Clarification

When the research request is ambiguous, use `AskUserQuestion` with structured options:

```
AskUserQuestion:
  questions:
    - question: "What depth of research do you need?"
      header: "Depth"
      multiSelect: false
      options:
        - label: "Quick Overview (Recommended)"
          description: "3-5 sources, executive summary format"
        - label: "Standard Analysis"
          description: "8-12 sources, structured report with sections"
        - label: "Comprehensive Deep-Dive"
          description: "15+ sources, academic-level with full citations"

    - question: "What is the primary purpose of this research?"
      header: "Purpose"
      multiSelect: false
      options:
        - label: "Decision Support"
          description: "Actionable insights for making a choice"
        - label: "Learning & Understanding"
          description: "Educational overview of the topic"
        - label: "Competitive Analysis"
          description: "Compare alternatives or competitors"
        - label: "Technical Evaluation"
          description: "Assess feasibility or implementation"

    - question: "What output format do you prefer?"
      header: "Format"
      multiSelect: false
      options:
        - label: "Structured Report (Recommended)"
          description: "Sections with headers, detailed prose"
        - label: "Executive Summary"
          description: "Concise bullets, key findings only"
        - label: "Comparison Table"
          description: "Side-by-side analysis format"
```

#### When to Skip Clarification

Skip `AskUserQuestion` if the request already specifies:
- Clear scope and depth
- Specific output format
- Time constraints
- Target audience

**Clarifying Questions Template (for complex cases):**
- What is the primary purpose? (Decision-making, learning, comparison)
- What time period is relevant? (Historical, current, future projections)
- What depth is needed? (Executive summary, technical deep-dive, comprehensive analysis)
- Are there specific regions, industries, or constraints?
- What format is preferred? (Report, bullet points, comparative table)

**Output:** A Research Brief that serves as the "North Star" for all subsequent work and the best output format: whether it should be wordy and explanatory or compressed and information dense.

#### Saving the Research Brief

After clarification, persist the research brief:

```
mcp__ag3ntum__Write:
  file_path: workspace/research/research_brief.md
  content: |
    # Research Brief: [Topic]

    ## Original Request
    [User's original query]

    ## Clarified Scope
    - **Purpose**: [Decision/Learning/Analysis]
    - **Depth**: [Quick/Standard/Comprehensive]
    - **Time Horizon**: [Historical/Current/Projections]
    - **Constraints**: [Region/Industry/Budget]

    ## Output Specifications
    - **Format**: [Report/Summary/Table]
    - **Audience**: [Technical/Executive/General]
    - **Length**: [Approximate target]

    ## Key Questions to Answer
    1. [Primary question]
    2. [Secondary question]
    3. [Tertiary question]
```

### Phase 2: Perspective-Guided Planning

Generate a multi-perspective research plan.

**Tools for Phase 2:**
| Action | Tool | Purpose |
|--------|------|---------|
| Discover perspectives | `WebSearch` | Find expert viewpoints |
| Track planning progress | `TodoWrite` | Mark planning as in_progress |
| Save research plan | `mcp__ag3ntum__Write` | Persist to workspace/research/plan.md |
| Add section tasks | `TodoWrite` | Create tasks for each section |

**Algorithm:**
1. **Mark TodoWrite** - Set "Create research plan" to `in_progress`

2. **Perspective Discovery**: Identify 3-5 distinct expert viewpoints relevant to the topic
   - Example for "AI Regulation": Legal Scholar, Tech Entrepreneur, Ethics Researcher, Policy Maker, Consumer Advocate
   - Use `WebSearch` to discover stakeholder groups if unfamiliar

3. **Question Generation**: For each perspective, generate research questions
   - What would a [Perspective] want to know about this topic?
   - What concerns would a [Perspective] raise?
   - What evidence would convince a [Perspective]?

4. **Synthesis**: Merge questions into a hierarchical outline
   - Group by theme, not by perspective
   - Identify dependencies (what must be researched first)
   - Estimate complexity and allocate effort accordingly

5. **Update TodoWrite** - Add tasks for each research section:
   ```
   TodoWrite - add new todos dynamically:
   - "Research: [Section 1 Topic]" - pending
   - "Research: [Section 2 Topic]" - pending
   - "Research: [Section 3 Topic]" - pending
   - "Conflict resolution and verification" - pending
   - "Final synthesis and report" - pending
   ```

6. **Mark TodoWrite** - Set "Create research plan" to `completed`

**Output:** A structured research plan with sections, sub-questions, and dependencies.

#### Saving the Research Plan

```
mcp__ag3ntum__Write:
  file_path: workspace/research/plan.md
  content: |
    # Research Plan: [Topic]

    ## Perspectives Considered
    1. [Perspective 1] - [Why relevant]
    2. [Perspective 2] - [Why relevant]
    3. [Perspective 3] - [Why relevant]

    ## Research Sections

    ### Section 1: [Topic]
    **Dependencies**: None (can start immediately)
    **Parallelizable**: Yes
    **Questions**:
    - Q1.1: [Specific question]
    - Q1.2: [Specific question]

    ### Section 2: [Topic]
    **Dependencies**: None
    **Parallelizable**: Yes
    **Questions**:
    - Q2.1: [Specific question]
    - Q2.2: [Specific question]

    ### Section 3: [Topic]
    **Dependencies**: Section 1 (builds on findings)
    **Parallelizable**: No - wait for Section 1
    **Questions**:
    - Q3.1: [Specific question]

    ## Execution Order
    1. Parallel: Sections 1, 2
    2. Sequential: Section 3 (after Section 1)
    3. Final: Conflict resolution, synthesis
```

### Phase 3: Recursive Execution

Execute the plan using depth-first, recursive search logic.

**Tools for Phase 3:**
| Action | Tool | Purpose |
|--------|------|---------|
| Discover sources | `WebSearch` | Find relevant URLs and snippets |
| Extract content | `mcp__ag3ntum__WebFetch` | Get full content from URLs |
| Launch parallel research | `Task` | Run independent sections concurrently |
| Save section findings | `mcp__ag3ntum__Write` | Persist to workspace/research/[section].md |
| Track URL history | `mcp__ag3ntum__Write` | Append to workspace/research/urls_visited.txt |
| Read accumulated data | `mcp__ag3ntum__Read` | Load previous findings |
| Track progress | `TodoWrite` | Update section status |

#### Execution Strategy Decision Tree

```
┌─────────────────────────────────────────────────────────┐
│           PHASE 3 EXECUTION STRATEGY                    │
├─────────────────────────────────────────────────────────┤
│  Are there 2+ independent sections (no dependencies)?   │
│       │                                                 │
│       ├── YES → Launch with Task tool IN PARALLEL      │
│       │         (single message, multiple Task calls)   │
│       │                                                 │
│       └── NO  → Execute SEQUENTIALLY in main agent     │
│                 (wait for dependent sections first)     │
└─────────────────────────────────────────────────────────┘
```

#### Parallel Execution (Recommended for Independent Sections)

Launch ALL independent sections in ONE message with multiple Task calls:

```
// Single message with 3 parallel Task calls:

Task:
  subagent_type: "general-purpose"
  description: "Research Section 1: [Topic]"
  prompt: |
    ## Research Assignment: [Section 1 Topic]

    ### Questions to Answer
    1. [Q1.1 from plan]
    2. [Q1.2 from plan]

    ### Execution
    1. Use WebSearch to find 3-5 authoritative sources
    2. Use mcp__ag3ntum__WebFetch to extract key content from each URL
    3. Apply Chain of Density summarization
    4. Save findings to: workspace/research/section_1.md

    ### Citation Format
    Every claim must have: [Source Title](URL) - Date

Task:
  subagent_type: "general-purpose"
  description: "Research Section 2: [Topic]"
  prompt: |
    [Same structure for Section 2, saves to section_2.md]

Task:
  subagent_type: "general-purpose"
  description: "Research Section 3: [Topic]"
  prompt: |
    [Same structure for Section 3, saves to section_3.md]
```

**After parallel tasks complete:**
```
// Update TodoWrite - mark all sections completed
TodoWrite:
  - "Research: Section 1" - completed
  - "Research: Section 2" - completed
  - "Research: Section 3" - completed
  - "Conflict resolution" - in_progress

// Read all findings into main context
mcp__ag3ntum__Read workspace/research/section_1.md
mcp__ag3ntum__Read workspace/research/section_2.md
mcp__ag3ntum__Read workspace/research/section_3.md
```

#### Sequential Execution (For Each Section)

**For Each Section:**

1. **Mark TodoWrite** - Set section to `in_progress`

2. **Query Formulation**
   - Generate 3-5 distinct search queries
   - Vary query structure: broad overview, specific data points, expert opinions
   - Use advanced operators when appropriate: `site:edu`, `filetype:pdf`, date ranges

3. **Content Retrieval with WebSearch + WebFetch**

   ```
   // Step 1: Discover sources
   WebSearch:
     query: "[topic] comprehensive analysis 2025 2026"

   // Step 2: Extract content from promising URLs
   mcp__ag3ntum__WebFetch:
     url: "https://example.com/article"
     prompt: "Extract key facts, statistics, and expert opinions about [topic]. Include specific numbers, dates, and names."

   // Step 3: Track visited URLs (avoid redundancy)
   mcp__ag3ntum__Write:
     file_path: workspace/research/urls_visited.txt
     content: |
       [append to existing]
       https://example.com/article - [date] - [relevance: high/medium/low]
   ```

   - Prioritize authoritative sources (.gov, .edu, well-known scientific or business sources, primary sources, peer-reviewed, community-driven resources)
   - Track all URLs visited to avoid redundancy

4. **Chain of Density Processing**
   Apply iterative data compaction and compression to maximize information density:
   ```
   Pass 1: Generate standard summary of source
   Pass 2: Identify 1-3 missing entities (facts, numbers, names) from source
   Pass 3: Rewrite summary to include missing entities WITHOUT increasing length
   Pass 4: Repeat until summary is maximally dense
   ```

5. **Save Intermediate Findings**

   ```
   mcp__ag3ntum__Write:
     file_path: workspace/research/section_[N]_[topic].md
     content: |
       # Section [N]: [Topic]

       ## Key Findings
       - Finding 1 ([Source](URL))
       - Finding 2 ([Source](URL))

       ## Data Points
       | Metric | Value | Source |
       |--------|-------|--------|
       | [X]    | [Y]   | [URL]  |

       ## Expert Opinions
       > "Quote" - Expert Name, Organization ([Source](URL))

       ## Gaps Identified
       - [What's still missing]

       ## Sources Used
       1. [Title](URL) - Date - Relevance: High
   ```

6. **Reflexion Check**
   After each search cycle, ask:
   - Does this answer the section's core question?
   - What specific information is still missing?
   - Did I encounter new concepts requiring deeper investigation?
   - Is it practical and actionable?

   **If incomplete:** Generate refined queries and recurse (max depth: 3)
   **If complete:** Mark section done in TodoWrite and proceed

7. **Mark TodoWrite** - Set section to `completed`

8. **Stopping Criteria**
   - Information Gain < Threshold (new sources overlap >90% with existing knowledge)
   - All required "slots" filled (specific data points identified in plan)
   - Maximum recursion depth reached
   - Logical completeness achieved

### Phase 4: Synthesis & Conflict Resolution

**Tools for Phase 4:**
| Action | Tool | Purpose |
|--------|------|---------|
| Read all section findings | `mcp__ag3ntum__Read` | Load completed research |
| Search local research | `mcp__ag3ntum__Grep` | Find specific facts across files |
| Verify disputed claims | `WebSearch` | Find authoritative sources |
| Extract verification data | `mcp__ag3ntum__WebFetch` | Get content for disputed claims |
| Save conflict analysis | `mcp__ag3ntum__Write` | Document resolutions |
| Write final report | `mcp__ag3ntum__Write` | Save to workspace/research/final_report.md |
| Track progress | `TodoWrite` | Mark synthesis phases |

**Mark TodoWrite** - Set "Conflict resolution and verification" to `in_progress`

#### Loading All Research Findings

```
// Read all section files (can be parallel Read calls)
mcp__ag3ntum__Read workspace/research/section_1.md
mcp__ag3ntum__Read workspace/research/section_2.md
mcp__ag3ntum__Read workspace/research/section_3.md

// Search for specific facts across all research
mcp__ag3ntum__Grep:
  pattern: "market size|growth rate|revenue"
  path: workspace/research/
```

#### Conflict Detection

- Monitor for semantic contradictions between sources
- Flag numerical discrepancies, opposing conclusions, or timeline conflicts

```
// If conflicts found, document them:
mcp__ag3ntum__Write:
  file_path: workspace/research/conflicts.md
  content: |
    # Detected Conflicts

    ## Conflict 1: [Topic]
    - **Source A**: [Claim] - [URL]
    - **Source B**: [Claim] - [URL]
    - **Status**: Pending verification
```

#### Conflict Resolution Protocol

1. **Verify**: Search specifically for the disputed claim using authoritative sources
   ```
   WebSearch:
     query: "[disputed claim] authoritative source site:gov OR site:edu"
   ```

2. **Contextualize**: Check if sources are from different time periods or contexts

3. **Adjudicate**: If conflict is genuine, report it transparently:
   > "Sources disagree on [topic]. Source A (2024) reports X, while Source B (2023) claims Y. The discrepancy may be due to [methodological differences/time lag/regional variation]."

**Mark TodoWrite** - Set "Conflict resolution" to `completed`, "Final synthesis" to `in_progress`

#### Report Assembly

```
mcp__ag3ntum__Write:
  file_path: workspace/research/final_report.md
  content: |
    # [Research Title]

    ## Executive Summary
    [2-3 paragraph overview]

    ## 1. [Section 1 Title]
    [Content with inline citations]

    ## 2. [Section 2 Title]
    [Content with inline citations]

    ## Key Findings & Implications
    [Synthesis]

    ## Limitations & Gaps
    [What could not be determined]

    ## Sources
    [Full citation list]
```

- Organize findings according to the research plan
- Ensure every claim has citation support
- Maintain consistent voice and academic tone
- Include appropriate hedging for uncertain claims

**Mark TodoWrite** - Set "Final synthesis and report" to `completed`

## Prompting Techniques

### Chain of Density Prompt
```
Summarize the following content in exactly 3 sentences.

Then, identify 2 specific facts (names, numbers, dates, technical terms) that were 
omitted but are important.

Finally, rewrite your summary to include these facts while keeping it to 3 sentences.
```

### Reflexion Prompt
```
I just searched for [query] and found [summary of results].

Critique: Did I find what I needed for [section goal]?
- What specific information is still missing?
- Was my query too broad or too narrow?
- What alternative search terms might yield better results?

Next action: [Continue/Refine query/Move to next section]
```

### Verification Prompt
```
I have conflicting information:
- Source A claims: [claim]
- Source B claims: [claim]

Search specifically for authoritative sources on this disputed point.
Prioritize: government data, academic papers, primary source documents.
Report findings with confidence assessment.
```

## Quality Standards

### Citation Requirements
- Every factual claim must have a source
- Include URL, title, and publication date when available
- Distinguish between primary and secondary sources
- Note when information could not be independently verified

### Prohibited Behaviors
- Never invent or fabricate sources
- Never use vague attributions ("some experts say") without citation
- Never present disputed claims as settled facts
- Never stop research prematurely without documenting gaps
- Never ignore contradictory evidence

### Report Structure
```markdown
# [Title]

## Executive Summary
[2-3 paragraph overview of key findings]

## 1. [Section Title]
### 1.1 [Subsection]
[Content with inline citations]

## 2. [Section Title]
...

## Key Findings & Implications
[Synthesis of most important discoveries]

## Limitations & Gaps
[Acknowledge what could not be determined]

## Sources
[Full citation list]
```

## Implementation Patterns

### For Simple Research (1-3 sources needed)
1. Skip extensive planning
2. Direct search → summarize → cite
3. Brief reflexion check

### For Medium Research (5-10 sources)
1. Quick scoping (identify 2-3 perspectives)
2. Generate focused plan (3-5 sections)
3. Execute with 1 level of recursion
4. Synthesize with conflict check

### For Deep Research (10+ sources, complex topic)
1. Full contextual scoping with clarification
2. Perspective-guided planning (3-5 perspectives)
3. Recursive execution with full reflexion loops
4. Comprehensive conflict resolution
5. Multi-section report with limitations analysis

## Tool Integration

### Complete Tool Reference

| Tool | When to Use | Key Parameters |
|------|-------------|----------------|
| `WebSearch` | Discover sources, find URLs | `query` - use operators like `site:edu` |
| `mcp__ag3ntum__WebFetch` | Extract content from found URLs | `url`, `prompt` - specify what to extract |
| `AskUserQuestion` | Clarify scope, get user decisions | `questions` with `options` array |
| `TodoWrite` | Track all phases, show progress | `todos` array with `status` |
| `Task` | Parallel research threads | `subagent_type`, `prompt`, launch multiple in ONE message |
| `mcp__ag3ntum__Write` | Save findings, plans, reports | `file_path`, `content` |
| `mcp__ag3ntum__Read` | Load saved research | `file_path` |
| `mcp__ag3ntum__Grep` | Search across research files | `pattern`, `path` |

### WebSearch Best Practices

```
// Broad discovery
WebSearch: query: "[topic] overview comprehensive guide 2025 2026"

// Specific data
WebSearch: query: "[topic] statistics data report site:gov OR site:edu"

// Expert opinions
WebSearch: query: "[topic] expert analysis research paper"

// Counterarguments
WebSearch: query: "[topic] criticism concerns problems limitations"

// Regional/temporal
WebSearch: query: "[topic] [region] [year] regulation policy"
```

### WebFetch Best Practices

```
mcp__ag3ntum__WebFetch:
  url: "[URL from WebSearch results]"
  prompt: |
    Extract from this page:
    1. Key statistics and numbers (with context)
    2. Main arguments or findings
    3. Expert quotes with attribution
    4. Methodology if this is research
    5. Date of publication or data

    Format as structured bullet points.
```

### Task Tool for Parallel Research

**CRITICAL**: Launch ALL independent sections in a SINGLE message:

```
// CORRECT - One message with multiple Task calls:
[Message containing:]
  Task 1: subagent_type="general-purpose", description="Research Section A", ...
  Task 2: subagent_type="general-purpose", description="Research Section B", ...
  Task 3: subagent_type="general-purpose", description="Research Section C", ...

// WRONG - Separate messages (loses parallelism):
[Message 1:] Task for Section A
[Message 2:] Task for Section B  // This waits for Task 1!
```

### File Organization Pattern

```
workspace/research/
├── research_brief.md      # Phase 1 output
├── plan.md                # Phase 2 output
├── urls_visited.txt       # Tracking (append-only)
├── section_1_[topic].md   # Phase 3 outputs
├── section_2_[topic].md
├── section_3_[topic].md
├── conflicts.md           # Phase 4 intermediate
└── final_report.md        # Final output
```

### Source Evaluation Criteria

1. **Search Strategy**
   - Start broad, then narrow based on findings
   - Use multiple query formulations for the same concept
   - Search for counterarguments explicitly

2. **Source Priority** (highest to lowest)
   - Government sources (.gov)
   - Academic institutions (.edu)
   - Peer-reviewed journals
   - Established news organizations
   - Industry reports from recognized firms
   - Community-driven resources (Wikipedia for overview only)

3. **Content Extraction**
   - Focus on extracting specific facts, not general summaries
   - Note methodology and sample sizes for studies
   - Capture direct quotes sparingly but accurately

---

## Adaptation Notes

- Scale depth to user needs and time constraints
- Explicitly state when comprehensive research would require more resources
- Offer to continue research in follow-up if initial scope is insufficient
- Maintain transparency about confidence levels throughout

---

## Quick Start Checklist

1. ☐ **TodoWrite** - Create initial task list immediately
2. ☐ **AskUserQuestion** - Clarify scope if ambiguous
3. ☐ **mcp__ag3ntum__Write** - Save research brief
4. ☐ **WebSearch** - Discover perspectives for planning
5. ☐ **TodoWrite** - Add section tasks dynamically
6. ☐ **mcp__ag3ntum__Write** - Save research plan
7. ☐ **Task** (parallel) - Launch independent sections in ONE message
8. ☐ **mcp__ag3ntum__Read** - Collect all section outputs
9. ☐ **WebSearch/WebFetch** - Resolve any conflicts
10. ☐ **mcp__ag3ntum__Write** - Save final report
11. ☐ **TodoWrite** - Mark all tasks completed