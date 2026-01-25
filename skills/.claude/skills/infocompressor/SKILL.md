---
name: infocompressor
description: |
  Use when the user needs to compress, condense, or summarize lengthy content while preserving ALL
  critical details. Triggers: "compress this", "make it shorter but keep everything", "create a
  cheat sheet", "dense summary", "reference format", "compact version", "information-dense". NOT
  for casual summaries—only when user explicitly wants maximum density with zero data loss.
---
# Information Compression Framework

## Core Principle

**Density = Essential Elements ÷ Character Count**

The goal: maximize semantic content per unit of text while preserving instant human readability.

---

## What Constitutes "Essential Information"

| Category | Examples | Priority |
|----------|----------|----------|
| **Identifiers** | Names, IDs, versions, dates, codes | Critical |
| **Values** | Numbers, measurements, thresholds, percentages | Critical |
| **Entities** | People, systems, components, organizations | Critical |
| **Relationships** | X→Y, A contains B, P depends on Q | High |
| **Causality** | If X then Y, because, therefore, triggers | High |
| **Attributes** | Properties, states, configurations, types | High |
| **Actions/Decisions** | What was done, chosen, rejected | High |
| **Sequences** | Order, steps, pipelines, workflows | Medium-High |
| **Inputs/Outputs** | What enters, what exits, parameters | Medium-High |
| **Constraints** | Limits, conditions, exceptions, rules | Medium |

---

## The Transformation Process

### Phase 1: Extraction Pass

**Read and tag every instance of:**
```
[E] Entity/Name
[V] Value/Number
[I] Identifier/Code
[R] Relationship
[C] Causality/Condition
[A] Attribute/Property
[D] Decision/Action
[S] Sequence step
[IO] Input or Output
```

### Phase 2: Redundancy Elimination

**Remove:**
- Filler phrases ("It is important to note that...")
- Repetition of same information in different words
- Obvious/inferable context
- Transitional prose ("Moving on to the next topic...")
- Hedging language ("perhaps," "might," "it seems")
- Attribution phrases when source is clear

### Phase 3: Structural Transformation

**Convert prose patterns to notation:**

| Original Pattern | Compressed Form |
|-----------------|-----------------|
| "X causes Y" | X → Y |
| "X results in Y" | X → Y |
| "X contains A, B, and C" | X: [A, B, C] |
| "If X then Y, otherwise Z" | X ? Y : Z |
| "X depends on Y" | X ← Y |
| "X is related to Y" | X ↔ Y |
| "X consists of Y" | X = {Y} |
| "X equals Y" | X = Y |
| "X is greater than Y" | X > Y |
| "X becomes Y" | X → Y |
| "from X to Y" | X..Y |
| "X and Y" | X + Y |
| "X or Y" | X \| Y |
| "not X" | ¬X or !X |

### Phase 4: Format Selection

**Choose based on information structure:**

```
HIERARCHICAL DATA → Nested indentation
  Component
    Sub-component
      Property: value

SEQUENTIAL/WORKFLOW → Arrow chains
  Step1 → Step2 → Step3 → Output

COMPARATIVE DATA → Tables
  | Entity | Attr1 | Attr2 |

KEY-VALUE PAIRS → Colon notation
  name: value
  config: {a:1, b:2}

CONDITIONAL LOGIC → Decision notation
  condition ? result_true : result_false

MULTIPLE CATEGORIES → Section headers
  ## Category
  content
```

---

## Compression Notation System

### Symbols & Operators
```
→  leads to, causes, outputs, then
←  depends on, requires, inputs from
↔  bidirectional relationship
|  or, alternative
&  and, combined with
!  not, negation
?  conditional, if
:  has property, equals, contains
::  type of, instance of
=  equals, defined as
≈  approximately
>  greater, more than
<  less, fewer than
≥≤ greater/less or equal
∅  none, empty, null
∞  unlimited, unbounded
Δ  change, delta, difference
#  count, number of
@  at, located, reference
*  important, key, required
~  approximately, around
+  plus, addition, includes
-  minus, excludes, removed
/  per, divided by, ratio
[]  optional, array, list
{}  set, group, object
()  grouping, parameters
```

### Abbreviations (context-dependent)
```
cfg=config  fn=function  attr=attribute
src=source  dst=destination  ref=reference
req=required  opt=optional  def=default
in=input  out=output  err=error
init=initialize  exec=execute  ret=return
prev=previous  curr=current  next=next
min/max  avg  cnt=count  len=length
```

---

## Template Structures

### For Technical Documentation
```
## [Component Name]
Purpose: [single line]
Type: [classification]
Inputs: [list]
Outputs: [list]
Config: {key:val, key:val}
Dependencies: [list]
Flow: step1 → step2 → step3
Constraints: [conditions]
```

### For Process/Workflow
```
## [Process Name]
Trigger: [what initiates]
Steps:
  1. Action → Result
  2. Action → Result
  3. Decision? → PathA : PathB
End state: [outcome]
```

### For Entity/Concept
```
## [Entity]
Definition: [one line]
Properties:
  attr1: value
  attr2: value
Relationships:
  → produces [X]
  ← requires [Y]
  ↔ interacts [Z]
```

### For Decision/Analysis
```
## [Decision Point]
Context: [situation]
Options: [A | B | C]
Criteria: [evaluation factors]
Choice: [selected] because [reason]
Outcome: [result]
```

---

## Transformation Examples

### Example 1: Technical Prose → Dense Format

**Original (847 chars):**
> The authentication system uses a multi-factor approach that combines something the user knows (their password) with something they have (a mobile device for receiving one-time codes). When a user attempts to log in, the system first validates their username and password against the database. If this primary authentication succeeds, the system then generates a six-digit code that expires after 30 seconds and sends it to the user's registered mobile number. The user must enter this code within the time limit to complete the authentication process. Failed attempts are logged and after five consecutive failures, the account is temporarily locked for 15 minutes.

**Compressed (312 chars):**
```
## Auth System
Type: Multi-factor (knowledge + possession)
Flow:
  1. User submits credentials
  2. Validate username+password → DB
  3. Success? → Generate 6-digit OTP (30s TTL) → SMS to registered mobile
  4. User enters OTP within TTL → Authenticated
Security:
  - Failed attempts: logged
  - 5 consecutive fails → account locked 15min
```

**Compression ratio: 63%**

---

### Example 2: Business Process → Dense Format

**Original (612 chars):**
> When a customer submits a refund request, the customer service team first checks if the purchase was made within the last 30 days. If it was, they then verify that the product is in its original condition and packaging. Products that meet both criteria are approved for a full refund, which is processed within 5-7 business days back to the original payment method. Products purchased more than 30 days ago but less than 90 days may be eligible for store credit instead of a cash refund, subject to manager approval.

**Compressed (267 chars):**
```
## Refund Process
Request → Check purchase date:
  ≤30 days + original condition → Full refund (5-7 days, original payment)
  30-90 days + original condition → Store credit (req manager approval)
  >90 days → Denied
Requirements: original condition + packaging
```

**Compression ratio: 56%**

---

## Master Instructions

### Step-by-Step Protocol

```
1. SCAN
   - Read entire source once
   - Identify information type (technical/process/narrative/analytical)
   
2. EXTRACT
   - Mark all entities, values, identifiers
   - Mark all relationships and causality
   - Mark all decisions and sequences
   
3. ELIMINATE
   - Remove redundancy
   - Remove filler/transitions
   - Remove inferable context
   
4. STRUCTURE
   - Choose optimal format for info type
   - Group related elements
   - Establish hierarchy
   
5. NOTATE
   - Apply symbols for relationships
   - Apply abbreviations consistently
   - Use key:value for attributes
   
6. VERIFY
   - Check: Can original be reconstructed?
   - Check: Is anything essential missing?
   - Check: Is it instantly readable?
   
7. REFINE
   - Adjust density vs readability balance
   - Ensure consistent notation
   - Add section headers if >5 elements
```

### Quality Checklist
```
□ All named entities preserved
□ All numerical values preserved  
□ All identifiers/codes preserved
□ All causal relationships captured
□ All decision points captured
□ Sequence/order maintained where relevant
□ No ambiguity introduced
□ Readable without reference to original
□ Notation used consistently
□ Compression ratio >40%
```

---

## Density Levels

Choose based on audience and use case:

| Level | Density | Use When |
|-------|---------|----------|
| **L1: Light** | 30-40% reduction | General audience, first read |
| **L2: Medium** | 40-60% reduction | Technical audience, reference |
| **L3: Heavy** | 60-75% reduction | Expert audience, quick lookup |
| **L4: Extreme** | 75%+ reduction | Personal notes, known context |

---

## Key Principles

1. **Preserve semantics, compress syntax** — meaning stays, words shrink
2. **Structure replaces prose** — formatting does the work of sentences
3. **Symbols beat words** — → is faster than "leads to"
4. **Hierarchy beats repetition** — indent once, don't restate parent
5. **Consistency enables speed** — same notation = instant recognition
6. **Completeness over brevity** — missing info = failed compression
7. **Readability is mandatory** — if it needs decoding, it's too dense

