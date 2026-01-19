---
name: deep-research
description: Enables systematic research (deep research, topic exploration) on any topic through web exploration, with intermediate result preservation and structured document output. 
---
# SKILL: Deep Research

## Overview

This skill enables systematic research on any topic through web exploration, with intermediate result preservation and structured document output. The workflow adapts based on topic type.

---

## Execution

1. You can use mcp__ag3ntum__AskUserQuestion or open questions to clarify 3-4 ambiguous or broad options or questions before start.
2. You can use Task tool to plan the exploration.
3. You can run concurrent subajects for research to optimize the performance.

---

## PHASE 1: INTAKE AND CLASSIFICATION

### 1.1 Initial User Clarification

Ask a maximum of **3 targeted questions** based on detected topic type. Do not ask generic questions—tailor to the specific research need.

**Universal questions (pick 1-2 relevant):**
- What decision or action will this research support?
- What is the single most important question to answer?
- Any constraints? (geography, budget, timeframe, industry)

### 1.2 Topic Type Detection

Analyze the user's request and classify into one of the following types. This determines the entire downstream workflow.

| Topic Type | Detection Signals | Example Requests |
|------------|-------------------|------------------|
| `COMPANY` | Company names, "competitor analysis", business terms | "Research Stripe", "Compare Notion vs Coda" |
| `PRODUCT` | Product names, "should I buy", "best X for Y" | "Best project management tool for startups" |
| `PERSON` | Names, "who is", biographical intent | "Research Jensen Huang's background" |
| `TECHNOLOGY` | Tech terms, "how does X work", implementation focus | "How does RAG work in LLMs" |
| `MARKET` | Industry terms, "market size", trends, forecasts | "AI chip market outlook 2025" |
| `EVENT` | Dates, "what happened", news focus | "CES 2025 announcements" |
| `HOW_TO` | "How to", process, tutorial intent | "How to set up CI/CD for Python" |
| `COMPARISON` | "vs", "compare", "difference between" | "PostgreSQL vs MySQL for analytics" |
| `GENERAL` | Does not fit above categories | Default fallback |

**Implementation:**
```
1. Parse user request for keywords and intent
2. Assign primary topic type
3. If ambiguous, ask ONE clarifying question
4. Log classification to research_log.md
```

---

## PHASE 2: RESEARCH EXECUTION

### 2.1 Directory Setup

**Always create this structure before starting research:**

```
/home/claude/research_[topic_slug]/
├── research_log.md          # Running log of all actions
├── sources/                  # Raw scraped content
│   ├── source_01.md
│   ├── source_02.md
│   └── ...
├── intermediate/             # Extracted data, notes
│   ├── findings.md
│   └── [topic-specific files]
└── output/                   # Final deliverables
    └── [final_report].md
```

**File naming convention:**
- `topic_slug`: lowercase, underscores, max 30 chars (e.g., `stripe_vs_paddle`)
- `source_XX.md`: numbered sequentially
- Timestamps: `YYYYMMDD_HHMMSS` when needed

### 2.2 Research Log Protocol

**Maintain `research_log.md` throughout execution:**

```markdown
# Research Log: [Topic]
Started: [timestamp]
Type: [COMPANY|PRODUCT|PERSON|...]
Core Question: [user's main question]

## Search History
| # | Query | Results Summary | Sources Scraped |
|---|-------|-----------------|-----------------|
| 1 | [query] | [2-3 word summary] | source_01.md |

## Key Decisions
- [Any judgment calls made during research]

## Gaps Identified
- [Questions that couldn't be answered]
```

### 2.3 Search Constraints

| Research Depth | Searches | Articles Scraped | Use When |
|----------------|----------|------------------|----------|
| Quick | 2-3 | 2-3 | Simple factual questions |
| Standard | 4-6 | 4-5 | Most requests (DEFAULT) |
| Deep | 7-10 | 6-8 | Complex comparisons, market analysis |

**Source selection priority:**
1. Official sources (company sites, government, documentation)
2. Industry publications (TechCrunch, Wired, industry-specific)
3. Major news outlets (Reuters, AP, BBC)
4. Reputable analysis (established blogs, expert commentary)

**Never scrape:** Forums, social media, content farms, paywalled content, aggregators

### 2.4 Source File Format

**Save each scraped source as `/sources/source_XX.md`:**

```markdown
# Source [XX]: [Article Title]

**URL:** [full URL]
**Scraped:** [timestamp]
**Type:** [official|news|analysis|documentation]
**Relevance:** [high|medium|low]

---

## Extracted Content

[Relevant excerpts only—not full article. Max 500 words per source.]

## Key Data Points

- [Specific facts, figures, quotes extracted]
- [...]

## Notes

[Any context about reliability, bias, or gaps]
```

---

## PHASE 3: TOPIC-SPECIFIC WORKFLOWS

### 3.1 COMPANY Research

**Clarification questions:**
- Are you evaluating them as a vendor, competitor, investment, or employer?
- What specific aspects matter most? (pricing, reliability, market position)

**Required searches:**
1. `[company name] official site` → Extract: products, pricing, positioning
2. `[company name] funding OR revenue 2024` → Extract: financials, scale
3. `[company name] reviews OR problems` → Extract: real-world issues
4. `[company name] competitors` → Extract: market context

**Intermediate file (`/intermediate/company_profile.md`):**
```markdown
# Company Profile: [Name]

## Basic Info
- Founded: 
- HQ: 
- Employees: 
- Funding/Revenue: 

## Product/Service
- Core offering: 
- Pricing model: 
- Key customers: 

## Market Position
- Main competitors: 
- Differentiation: 
- Market share (if available): 

## Red Flags / Concerns
- [Any issues found]

## Data Gaps
- [What couldn't be verified]
```

**Output format:** Company brief (1-2 pages) with recommendation based on user's evaluation context.

---

### 3.2 PRODUCT Research

**Clarification questions:**
- What's your primary use case?
- What's your budget range?
- Any must-have features or dealbreakers?

**Required searches:**
1. `best [product category] [year]` → Identify top options
2. `[product] review` → Real-world assessments
3. `[product] vs [alternative]` → Direct comparisons
4. `[product] problems OR issues` → Known limitations

**Intermediate file (`/intermediate/product_comparison.md`):**
```markdown
# Product Comparison: [Category]

## Options Evaluated
| Product | Price | Best For | Key Limitation |
|---------|-------|----------|----------------|
| | | | |

## Feature Matrix
| Feature | Product A | Product B | Product C |
|---------|-----------|-----------|-----------|
| | | | |

## User Sentiment Summary
- [Product A]: [positive/mixed/negative] — [1-line summary]
- [Product B]: ...
```

**Output format:** Comparison table + recommendation with clear reasoning tied to user's stated needs.

---

### 3.3 PERSON Research

**Clarification questions:**
- What context? (hiring, partnership, investment, general knowledge)
- Any specific aspects? (career history, expertise, reputation)

**Required searches:**
1. `[name] [role/company]` → Verify identity, current position
2. `[name] LinkedIn OR bio` → Career history
3. `[name] interview OR talk` → Perspectives, expertise
4. `[name] news [year]` → Recent activities

**Intermediate file (`/intermediate/person_profile.md`):**
```markdown
# Person Profile: [Name]

## Current Role
- Title: 
- Organization: 
- Since: 

## Background
- Previous roles: 
- Education: 
- Notable achievements: 

## Public Perspectives
- Key topics they discuss: 
- Notable quotes/positions: 

## Verification Notes
- [What could/couldn't be verified]
```

**Output format:** Executive bio (0.5-1 page) focused on user's context.

---

### 3.4 TECHNOLOGY Research

**Clarification questions:**
- What's your technical level? (beginner/intermediate/expert)
- Are you evaluating, implementing, or learning?

**Required searches:**
1. `[technology] explained` → Core concepts
2. `[technology] architecture OR how it works` → Technical details
3. `[technology] use cases` → Practical applications
4. `[technology] limitations OR challenges` → Realistic assessment

**Intermediate file (`/intermediate/tech_overview.md`):**
```markdown
# Technology Overview: [Name]

## What It Is
[2-3 sentence plain-language explanation]

## How It Works
[Core mechanism, appropriate to user's technical level]

## Key Components
- [Component 1]: [role]
- [Component 2]: [role]

## Use Cases
| Use Case | How It Applies | Example |
|----------|----------------|---------|
| | | |

## Limitations
- [Limitation 1]
- [Limitation 2]

## Implementation Considerations
- [If user is implementing]
```

**Output format:** Technical brief calibrated to user's stated level. Include diagram descriptions if helpful.

---

### 3.5 MARKET Research

**Clarification questions:**
- What's the business context? (entering market, investing, strategic planning)
- Geographic scope? (global, specific regions)

**Required searches:**
1. `[market] market size [year]` → Quantitative data
2. `[market] trends [year]` → Direction and drivers
3. `[market] major players` → Competitive landscape
4. `[market] forecast OR outlook` → Future projections

**Intermediate file (`/intermediate/market_analysis.md`):**
```markdown
# Market Analysis: [Market Name]

## Market Size
- Current: $[X] ([year])
- Projected: $[Y] by [year]
- CAGR: [X]%

## Key Segments
| Segment | Size/Share | Growth | Notes |
|---------|------------|--------|-------|
| | | | |

## Major Players
| Company | Est. Share | Positioning |
|---------|------------|-------------|
| | | |

## Trends
1. [Trend]: [Impact]
2. [Trend]: [Impact]

## Data Quality Notes
- [Source reliability assessment]
- [Conflicting data points]
```

**Output format:** Market brief (1-2 pages) with data tables and sourced projections.

---

### 3.6 COMPARISON Research

**Clarification questions:**
- What's the decision you're making?
- What factors matter most to you?

**Required searches:**
1. `[option A] vs [option B] [year]` → Direct comparisons
2. `[option A] review` → Individual assessment
3. `[option B] review` → Individual assessment
4. `[option A] [option B] migration OR switching` → Transition considerations

**Intermediate file (`/intermediate/comparison_matrix.md`):**
```markdown
# Comparison: [A] vs [B]

## Quick Verdict
[1-2 sentences: who should choose what]

## Comparison Matrix
| Factor | [A] | [B] | Winner |
|--------|-----|-----|--------|
| Price | | | |
| [Factor 2] | | | |
| [Factor 3] | | | |

## Detailed Breakdown

### [Factor 1]
- **[A]:** [specifics]
- **[B]:** [specifics]
- **Verdict:** [which wins and why]

## Edge Cases
- Choose [A] if: [specific scenario]
- Choose [B] if: [specific scenario]
```

**Output format:** Decision-focused comparison with clear recommendation tied to user's stated priorities.

---

### 3.7 HOW_TO Research

**Clarification questions:**
- What's your starting point? (complete beginner, some experience)
- What's the end goal? (learning, implementing specific thing)

**Required searches:**
1. `how to [task] guide` → Overview approaches
2. `[task] step by step` → Detailed instructions
3. `[task] common mistakes` → Pitfalls to avoid
4. `[task] tools OR software` → Required resources

**Intermediate file (`/intermediate/procedure_draft.md`):**
```markdown
# How To: [Task]

## Prerequisites
- [What user needs before starting]

## Tools/Resources Needed
- [Tool 1]: [purpose]
- [Tool 2]: [purpose]

## Steps
1. **[Step name]:** [Details]
2. **[Step name]:** [Details]

## Common Mistakes
- [Mistake]: [How to avoid]

## Verification
[How to confirm success]
```

**Output format:** Step-by-step guide calibrated to user's experience level.

---

### 3.8 EVENT Research

**Clarification questions:**
- What aspect interests you? (announcements, analysis, specific topics)

**Required searches:**
1. `[event] [year] announcements` → What happened
2. `[event] highlights OR recap` → Curated summaries
3. `[event] [specific topic]` → If user has focus area

**Output format:** Event summary with key announcements/outcomes organized by relevance to user's interest.

---

### 3.9 GENERAL Research (Fallback)

Use when topic doesn't fit other categories.

**Standard search pattern:**
1. `[topic] overview` → Establish basics
2. `[topic] [year]` → Recent developments
3. `[topic] [specific aspect from user question]` → Targeted answer

**Output format:** Direct answer to user's question with supporting context.

---

## PHASE 4: OUTPUT GENERATION

### 4.1 Standard Report Structure

```markdown
# [Topic]: Research Summary

**Prepared:** [date]
**Research Type:** [topic type]
**Core Question:** [user's main question]

---

## Bottom Line

[2-4 sentences: Direct answer to user's question + primary recommendation. No hedging.]

## Key Findings

### [Finding 1 Title]
[2-3 sentences with specific data. Source reference.]

### [Finding 2 Title]
[2-3 sentences with specific data. Source reference.]

### [Finding 3 Title]
[2-3 sentences with specific data. Source reference.]

## [Topic-Specific Section]
[Use appropriate section from topic type: comparison table, company profile, step-by-step guide, etc.]

## Practical Next Steps

1. [Actionable step 1]
2. [Actionable step 2]
3. [Actionable step 3]

## Limitations & Gaps

- [What couldn't be verified]
- [Areas needing deeper research]

---

## Sources

| # | Title | URL | Type |
|---|-------|-----|------|
| 1 | | | |
| 2 | | | |

---

*Research conducted [date]. For methodology and raw sources, see /research_[topic]/sources/*
```

### 4.2 Output Quality Rules

1. **Lead with the answer** — First paragraph must directly address user's question
2. **Data density** — Every paragraph contains at least one fact, figure, or actionable insight
3. **No filler** — Delete any sentence that doesn't help user decide or act
4. **Source everything** — All claims must trace to a saved source file
5. **Acknowledge gaps** — Explicitly state what couldn't be verified

### 4.3 File Delivery

**Final steps:**
1. Save final report to `/research_[topic]/output/[topic]_report.md`
2. Copy final report to `/mnt/user-data/outputs/[topic]_report.md`
3. Optionally convert to .docx if user prefers (use docx skill)
4. Present file to user with brief summary

---

## PHASE 5: EXECUTION CHECKLIST

Use this checklist for every research task:

```
□ 1. Classify topic type
□ 2. Ask clarification questions (max 3)
□ 3. Create directory structure
□ 4. Initialize research_log.md
□ 5. Execute searches (respect depth limits)
□ 6. Save sources to /sources/source_XX.md
□ 7. Create topic-specific intermediate file
□ 8. Update research_log.md with search history
□ 9. Generate final report using standard structure
□ 10. Save to output directory
□ 11. Copy to /mnt/user-data/outputs/
□ 12. Present file to user
```

---

## ERROR HANDLING

| Situation | Action |
|-----------|--------|
| Search returns no useful results | Try alternative query terms; log in research_log.md; note gap in output |
| Conflicting information found | Note both versions in intermediate file; present strongest-sourced version in output; mention conflict in Limitations |
| User question too broad | Ask ONE focusing question; if no response, research most common interpretation |
| Topic type ambiguous | Default to GENERAL workflow; adapt as information emerges |
| Cannot verify critical claim | Do not include in Key Findings; mention in Limitations section |

---

## INTEGRATION WITH OTHER SKILLS

- **docx skill:** Use for final document formatting if user requests Word format
- **xlsx skill:** Use for complex comparison matrices or data tables
- **pdf skill:** Use if user requests PDF output

**To invoke:** Read the relevant SKILL.md before generating output in that format.