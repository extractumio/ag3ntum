---
name: deep-research
description: Enables systematic research (deep research, topic exploration, data gathering) on any topic through web exploration, with intermediate result preservation and structured document output.
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

**For Each Section:**

1. **Query Formulation**
   - Generate 3-5 distinct search queries
   - Vary query structure: broad overview, specific data points, expert opinions
   - Use advanced operators when appropriate: `site:edu`, `filetype:pdf`, date ranges

2. **Content Retrieval**
   - Search and retrieve content from top results, make sure the content is relevant. If not -- repeat. 
   - Prioritize authoritative sources (.gov, .edu, well-known scientific or business sources, primary sources, peer-reviewed, community-driven resources)
   - Track all URLs visited to avoid redundancy (e.g. use external temporary file for it)

3. **Chain of Density Processing**
   Apply iterative data compaction and compression to maximize information density:
```
   Pass 1: Generate standard summary of source
   Pass 2: Identify 1-3 missing entities (facts, numbers, names) from source
   Pass 3: Rewrite summary to include missing entities WITHOUT increasing length
   Pass 4: Repeat until summary is maximally dense
```

4. **Reflexion Check**
   After each search cycle, ask:
   - Does this answer the section's core question?
   - What specific information is still missing?
   - Did I encounter new concepts requiring deeper investigation?
   - Is it practical and actionable?
   
   **If incomplete:** Generate refined queries and recurse (max depth: 3)
   **If complete:** Mark section done and proceed

5. **Stopping Criteria**
   - Information Gain < Threshold (new sources overlap >90% with existing knowledge)
   - All required "slots" filled (specific data points identified in plan)
   - Maximum recursion depth reached
   - Logical completeness achieved

### Phase 4: Synthesis & Conflict Resolution

**Conflict Detection:**
- Monitor for semantic contradictions between sources
- Flag numerical discrepancies, opposing conclusions, or timeline conflicts

**Conflict Resolution Protocol:**
1. **Verify**: Search specifically for the disputed claim using authoritative sources
2. **Contextualize**: Check if sources are from different time periods or contexts
3. **Adjudicate**: If conflict is genuine, report it transparently:
   > "Sources disagree on [topic]. Source A (2024) reports X, while Source B (2023) claims Y. The discrepancy may be due to [methodological differences/time lag/regional variation]."

**Report Assembly:**
- Organize findings according to the research plan
- Ensure every claim has citation support
- Maintain consistent voice and academic tone
- Include appropriate hedging for uncertain claims

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

When using web search and browsing tools:

1. **Search Strategy**
   - Start broad, then narrow based on findings
   - Use multiple query formulations for the same concept
   - Search for counterarguments explicitly

2. **Source Evaluation**
   - Prefer recent sources for rapidly evolving topics
   - Prefer authoritative domains for factual claims
   - Cross-reference claims across multiple sources

3. **Content Extraction**
   - Focus on extracting specific facts, not general summaries
   - Note methodology and sample sizes for studies
   - Capture direct quotes sparingly but accurately

## Adaptation Notes

- Scale depth to user needs and time constraints
- Explicitly state when comprehensive research would require more resources
- Offer to continue research in follow-up if initial scope is insufficient
- Maintain transparency about confidence levels throughout